// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

library TransferHelper {
    function safeTransfer(address token, address to, uint256 value) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(0xa9059cbb, to, value)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "TH: TRANSFER_FAILED");
    }

    function safeApprove(address token, address to, uint256 value) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(0x095ea7b3, to, value)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "TH: APPROVE_FAILED");
    }
}
