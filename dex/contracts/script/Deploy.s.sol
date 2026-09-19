// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/FlashArb.sol";

contract DeployScript is Script {
    // Aave V3 Pool on Base
    address constant AAVE_POOL = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;

    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        vm.startBroadcast(deployerKey);

        FlashArb flashArb = new FlashArb(AAVE_POOL);
        console.log("FlashArb deployed at:", address(flashArb));

        vm.stopBroadcast();
    }
}
