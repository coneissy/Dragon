// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

/**
 * @title DragonArbExecutor
 * @notice Minimal, fail-closed on-chain executor for permissionless arbitrage.
 *
 * The off-chain searcher calculates the route and expected result. This
 * contract enforces the important settlement invariant on-chain:
 * startingBalance + minimumProfit must be present after the route executes.
 * Routers are allowlisted and arbitrary calls are disabled.
 *
 * This contract does not implement a sandwich strategy and does not promise
 * profitability. It is an execution primitive for legitimate arbitrage.
 */
contract DragonArbExecutor {
    error NotOwner();
    error RouterNotAllowed();
    error TokenNotAllowed();
    error DeadlineExpired();
    error InsufficientProfit();
    error CallFailed();
    error InvalidRoute();

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

    /**
     * @dev Execute a bounded sequence of pre-built router calls.
     * Each call must target an allowlisted router. Token approvals are
     * intentionally handled by the caller/searcher and can be reset by the
     * caller between routes.
     */
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

        for (uint256 i = 0; i < routers.length; i++) {
            if (!allowedRouter[routers[i]]) revert RouterNotAllowed();
            (bool ok, ) = routers[i].call(calls[i]);
            if (!ok) revert CallFailed();
        }

        uint256 endingBalance = IERC20(profitToken).balanceOf(address(this));
        if (endingBalance < startingBalance + minimumProfit) revert InsufficientProfit();

        profit = endingBalance - startingBalance;
        emit ArbitrageExecuted(msg.sender, profitToken, startingBalance, endingBalance, profit);
    }

    function sweep(address token, address recipient, uint256 amount) external onlyOwner {
        if (recipient == address(0)) revert InvalidRoute();
        if (!IERC20(token).transfer(recipient, amount)) revert CallFailed();
    }
}
