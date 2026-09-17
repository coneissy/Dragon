// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

/**
 * @title DragonArbExecutor
 * @notice Fail-closed on-chain settlement primitive for decentralized arbitrage.
 *
 * The searcher supplies an already-constructed route. The contract enforces
 * allowlisted routers/tokens, a deadline, and a minimum increase in the profit
 * token balance. Every failed route reverts atomically.
 *
 * Router contracts are trusted infrastructure and MUST themselves enforce
 * swap-specific minimum outputs/deadlines. Dragon does not implement a
 * sandwich strategy.
 */
contract DragonArbExecutor {
    error NotOwner();
    error RouterNotAllowed();
    error TokenNotAllowed();
    error DeadlineExpired();
    error InsufficientProfit();
    error CallFailed();
    error InvalidRoute();
    error ArithmeticOverflow();

    address public immutable owner;
    mapping(address => bool) public allowedRouter;
    mapping(address => bool) public allowedToken;

    event RouterPermission(address indexed router, bool allowed);
    event TokenPermission(address indexed token, bool allowed);
    event ArbitrageExecuted(
        address indexed executor,
        address indexed profitToken,
        uint256 startingBalance,
        uint256 endingBalance,
        uint256 profit
    );

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert InvalidRoute();
        owner = initialOwner;
    }

    function setRouter(address router, bool allowed) external onlyOwner {
        if (router == address(0)) revert InvalidRoute();
        allowedRouter[router] = allowed;
        emit RouterPermission(router, allowed);
    }

    function setToken(address token, bool allowed) external onlyOwner {
        if (token == address(0)) revert InvalidRoute();
        allowedToken[token] = allowed;
        emit TokenPermission(token, allowed);
    }

    function execute(
        address profitToken,
        uint256 minimumProfit,
        uint256 deadline,
        address[] calldata routers,
        bytes[] calldata calls
    ) external returns (uint256 profit) {
        if (!allowedToken[profitToken]) revert TokenNotAllowed();
        if (block.timestamp > deadline) revert DeadlineExpired();
        if (routers.length == 0 || routers.length != calls.length) revert InvalidRoute();

        uint256 startingBalance = IERC20(profitToken).balanceOf(address(this));
        if (minimumProfit > type(uint256).max - startingBalance) revert ArithmeticOverflow();

        for (uint256 i = 0; i < routers.length; i++) {
            address router = routers[i];
            if (!allowedRouter[router] || router.code.length == 0) revert RouterNotAllowed();
            (bool ok, ) = router.call(calls[i]);
            if (!ok) revert CallFailed();
        }

        uint256 endingBalance = IERC20(profitToken).balanceOf(address(this));
        uint256 requiredEndingBalance = startingBalance + minimumProfit;
        if (endingBalance < requiredEndingBalance) revert InsufficientProfit();

        profit = endingBalance - startingBalance;
        emit ArbitrageExecuted(msg.sender, profitToken, startingBalance, endingBalance, profit);
    }

    function sweep(address token, address recipient, uint256 amount) external onlyOwner {
        if (recipient == address(0)) revert InvalidRoute();
        if (!IERC20(token).transfer(recipient, amount)) revert CallFailed();
    }
}
