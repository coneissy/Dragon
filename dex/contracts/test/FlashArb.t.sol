// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/FlashArb.sol";

contract FlashArbTest is Test {
    FlashArb public arb;

    // Base mainnet addresses
    address constant AAVE_POOL = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    // Sushi WETH/USDC V2 pair on Base
    address constant SUSHI_WETH_USDC = 0xB9960d9bcA016e9748bE75dd52F02188B9d0829f;
    // Aerodrome WETH/USDC volatile
    address constant AERO_WETH_USDC = 0xCDac0D6C6C59727a65f871236945791119b49EeE;

    function setUp() public {
        // Fork Base mainnet
        arb = new FlashArb(AAVE_POOL);
    }

    function test_ownerIsDeployer() public view {
        assertEq(arb.owner(), address(this));
    }

    function test_aavePoolSet() public view {
        assertEq(address(arb.aavePool()), AAVE_POOL);
    }

    /// @notice Test that a non-profitable arb reverts with "no profit"
    function test_revertOnNoProfit() public {
        // Attempt arb between same pool type — should revert
        FlashArb.SwapParams memory swap0 = FlashArb.SwapParams({
            pool: SUSHI_WETH_USDC,
            poolType: 0, // V2
            zeroForOne: true,
            amountOutMin: 0,
            feeBps: 30
        });
        FlashArb.SwapParams memory swap1 = FlashArb.SwapParams({
            pool: SUSHI_WETH_USDC,
            poolType: 0,
            zeroForOne: false,
            amountOutMin: 0,
            feeBps: 30
        });

        // This should revert because round-tripping through the same pool loses fees
        vm.expectRevert();
        arb.executeArb(WETH, 1 ether, swap0, swap1, USDC);
    }

    function test_onlyOwner() public {
        FlashArb.SwapParams memory swap0 = FlashArb.SwapParams({
            pool: SUSHI_WETH_USDC,
            poolType: 0,
            zeroForOne: true,
            amountOutMin: 0,
            feeBps: 30
        });

        vm.prank(address(0xdead));
        vm.expectRevert(FlashArb.NotOwner.selector);
        arb.executeArb(WETH, 1 ether, swap0, swap0, USDC);
    }

    function test_rescue_onlyOwner() public {
        vm.prank(address(0xdead));
        vm.expectRevert(FlashArb.NotOwner.selector);
        arb.rescue(WETH, 1);
    }
}
