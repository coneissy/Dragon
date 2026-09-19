// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IAavePool, IFlashLoanSimpleReceiver} from "./interfaces/IAavePool.sol";
import {IUniswapV2Pair} from "./interfaces/IUniswapV2Pair.sol";
import {IAerodromeV2Pair} from "./interfaces/IAerodromeV2Pair.sol";
import {IUniswapV3Pool} from "./interfaces/IUniswapV3Pool.sol";
import {TransferHelper} from "./libraries/TransferHelper.sol";

/// @title FlashArb — Atomic flashloan arbitrage across DEXes on Base
contract FlashArb is IFlashLoanSimpleReceiver {
    address public immutable owner;
    IAavePool public immutable aavePool;

    uint8 constant POOL_V2 = 0;
    uint8 constant POOL_V3 = 1;
    uint8 constant POOL_V2_AERO = 2;

    uint160 constant MIN_SQRT_RATIO = 4295128739;
    uint160 constant MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342;

    /// @dev Set before V3 swap, checked in callback, cleared after
    address private _expectedV3Pool;

    struct SwapParams {
        address pool;
        uint8 poolType;
        bool zeroForOne;
        uint256 amountOutMin;
        uint24 feeBps; // V2 fee in bps (e.g. 30 = 0.3%), ignored for V3
    }

    error NotOwner();
    error NotAave();
    error NotSelf();
    error NoProfit();
    error Slippage();
    error UnknownPoolType();
    error UnauthorizedCallback();

    event ArbExecuted(address indexed token, uint256 amountIn, uint256 profit);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address _aavePool) {
        owner = msg.sender;
        aavePool = IAavePool(_aavePool);
    }

    function executeArb(
        address token,
        uint256 amount,
        SwapParams calldata swap0,
        SwapParams calldata swap1,
        address bridgeToken
    ) external onlyOwner {
        bytes memory params = abi.encode(swap0, swap1, bridgeToken);
        aavePool.flashLoanSimple(address(this), token, amount, params, 0);
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        if (msg.sender != address(aavePool)) revert NotAave();
        if (initiator != address(this)) revert NotSelf();

        (SwapParams memory swap0, SwapParams memory swap1, address bridgeToken) =
            abi.decode(params, (SwapParams, SwapParams, address));

        // Swap 0: A → B
        uint256 amountB = _swap(asset, amount, swap0);

        // Swap 1: B → A
        uint256 amountA = _swap(bridgeToken, amountB, swap1);

        // Repay flashloan
        uint256 amountOwed = amount + premium;
        if (amountA < amountOwed) revert NoProfit();

        TransferHelper.safeApprove(asset, address(aavePool), amountOwed);

        // Send profit to owner
        uint256 profit = amountA - amountOwed;
        if (profit > 0) {
            TransferHelper.safeTransfer(asset, owner, profit);
        }

        emit ArbExecuted(asset, amount, profit);
        return true;
    }

    function _swap(address tokenIn, uint256 amountIn, SwapParams memory params) internal returns (uint256 amountOut) {
        if (params.poolType == POOL_V2 || params.poolType == POOL_V2_AERO) {
            amountOut = _swapV2(tokenIn, amountIn, params.pool, params.zeroForOne, params.feeBps, params.poolType);
        } else if (params.poolType == POOL_V3) {
            amountOut = _swapV3(tokenIn, amountIn, params.pool, params.zeroForOne);
        } else {
            revert UnknownPoolType();
        }
        if (amountOut < params.amountOutMin) revert Slippage();
    }

    function _swapV2(
        address tokenIn,
        uint256 amountIn,
        address pool,
        bool zeroForOne,
        uint24 feeBps,
        uint8 poolType
    ) internal returns (uint256 amountOut) {
        uint256 reserveIn;
        uint256 reserveOut;

        if (poolType == POOL_V2_AERO) {
            (uint256 r0, uint256 r1,) = IAerodromeV2Pair(pool).getReserves();
            (reserveIn, reserveOut) = zeroForOne ? (r0, r1) : (r1, r0);
        } else {
            (uint112 r0, uint112 r1,) = IUniswapV2Pair(pool).getReserves();
            (reserveIn, reserveOut) = zeroForOne ? (uint256(r0), uint256(r1)) : (uint256(r1), uint256(r0));
        }

        uint256 feeMultiplier = 10000 - uint256(feeBps);
        uint256 amountInWithFee = amountIn * feeMultiplier;
        amountOut = (amountInWithFee * reserveOut) / (reserveIn * 10000 + amountInWithFee);

        TransferHelper.safeTransfer(tokenIn, pool, amountIn);
        (uint256 out0, uint256 out1) = zeroForOne
            ? (uint256(0), amountOut)
            : (amountOut, uint256(0));

        if (poolType == POOL_V2_AERO) {
            IAerodromeV2Pair(pool).swap(out0, out1, address(this), "");
        } else {
            IUniswapV2Pair(pool).swap(out0, out1, address(this), "");
        }
    }

    function _swapV3(
        address tokenIn,
        uint256 amountIn,
        address pool,
        bool zeroForOne
    ) internal returns (uint256 amountOut) {
        uint160 sqrtPriceLimit = zeroForOne ? MIN_SQRT_RATIO + 1 : MAX_SQRT_RATIO - 1;

        _expectedV3Pool = pool;

        (int256 amount0, int256 amount1) = IUniswapV3Pool(pool).swap(
            address(this),
            zeroForOne,
            int256(amountIn),
            sqrtPriceLimit,
            abi.encode(tokenIn, amountIn)
        );

        _expectedV3Pool = address(0);

        amountOut = uint256(-(zeroForOne ? amount1 : amount0));
    }

    function uniswapV3SwapCallback(
        int256 amount0Delta,
        int256 amount1Delta,
        bytes calldata data
    ) external {
        _v3SwapCallback(amount0Delta, amount1Delta, data);
    }

    function pancakeV3SwapCallback(
        int256 amount0Delta,
        int256 amount1Delta,
        bytes calldata data
    ) external {
        _v3SwapCallback(amount0Delta, amount1Delta, data);
    }

    function _v3SwapCallback(
        int256 amount0Delta,
        int256 amount1Delta,
        bytes calldata data
    ) internal {
        if (msg.sender != _expectedV3Pool) revert UnauthorizedCallback();
        (address tokenIn,) = abi.decode(data, (address, uint256));
        uint256 amountToPay = amount0Delta > 0 ? uint256(amount0Delta) : uint256(amount1Delta);
        TransferHelper.safeTransfer(tokenIn, msg.sender, amountToPay);
    }

    function rescue(address token, uint256 amount) external onlyOwner {
        TransferHelper.safeTransfer(token, owner, amount);
    }

    receive() external payable {}
}
