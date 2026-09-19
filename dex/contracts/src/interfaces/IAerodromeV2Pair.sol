// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IAerodromeV2Pair {
    function getReserves() external view returns (uint256 reserve0, uint256 reserve1, uint256 blockTimestampLast);
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;
}
