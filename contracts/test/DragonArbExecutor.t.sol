// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {DragonArbExecutor} from "../src/DragonArbExecutor.sol";

contract MockToken {
    mapping(address => uint256) public balanceOf;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address, uint256) external pure returns (bool) { return true; }
    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract MockRouter {
    MockToken immutable token;
    uint256 immutable amount;

    constructor(MockToken token_, uint256 amount_) {
        token = token_;
        amount = amount_;
    }

    function execute() external {
        token.mint(msg.sender, amount);
    }
}

contract DragonArbExecutorTest {
    function testExecuteRequiresMinimumProfit() public {
        MockToken token = new MockToken();
        MockRouter router = new MockRouter(token, 2 ether);
        DragonArbExecutor executor = new DragonArbExecutor(address(this));

        executor.setToken(address(token), true);
        executor.setRouter(address(router), true);
        token.mint(address(executor), 10 ether);

        address[] memory routers = new address[](1);
        routers[0] = address(router);
        bytes[] memory calls = new bytes[](1);
        calls[0] = abi.encodeCall(MockRouter.execute, ());

        uint256 profit = executor.execute(
            address(token),
            1 ether,
            block.timestamp + 60,
            routers,
            calls
        );

        require(profit == 2 ether, "wrong profit");
        require(token.balanceOf(address(executor)) == 12 ether, "wrong ending balance");
    }

    function testRejectsUnprofitableRoute() public {
        MockToken token = new MockToken();
        MockRouter router = new MockRouter(token, 1 ether);
        DragonArbExecutor executor = new DragonArbExecutor(address(this));

        executor.setToken(address(token), true);
        executor.setRouter(address(router), true);
        token.mint(address(executor), 10 ether);

        address[] memory routers = new address[](1);
        routers[0] = address(router);
        bytes[] memory calls = new bytes[](1);
        calls[0] = abi.encodeCall(MockRouter.execute, ());

        (bool ok, ) = address(executor).call(
            abi.encodeCall(
                DragonArbExecutor.execute,
                (address(token), 2 ether, block.timestamp + 60, routers, calls)
            )
        );
        require(!ok, "unprofitable route was accepted");
    }
}
