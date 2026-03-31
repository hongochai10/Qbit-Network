"""State trie operations mixin for the Blockchain class.

Extracted from blockchain.py to reduce file size. These methods operate
on self._state_trie, self._balances, and self._sender_nonce which are
initialised by Blockchain.__init__.
"""

import re

_TOKEN_ID_RE = re.compile(r'^[0-9a-fA-F]{32}$')


def _validate_token_id(tid: str) -> bool:
    """Return True if *tid* is a safe 32-char hex token identifier."""
    return isinstance(tid, str) and bool(_TOKEN_ID_RE.match(tid))


class StateTrieMixin:
    """Mixin providing state trie rebuild, proof generation, and root access."""

    def _rebuild_state_trie(self):
        """Rebuild the state trie from current balances and nonces.

        This is called at the end of _append_block_inner after all state
        mutations are complete.  The trie covers:
          - balance:{address} -> 8-byte big-endian int
          - nonce:{address}   -> 8-byte big-endian int
        """
        trie = self._state_trie
        # Clear and rebuild -- simple and correct for QBit's scale.
        trie._entries.clear()
        for addr, bal in self._balances.items():
            trie.set(f"balance:{addr}", bal.to_bytes(8, 'big'))
        for addr, nonce in self._sender_nonce.items():
            # Nonces are always >= 0 in normal operation.
            # During rollback edge cases nonce can be -1 (no confirmed txs);
            # skip negative nonces to avoid encoding errors.
            if nonce >= 0:
                trie.set(f"nonce:{addr}", nonce.to_bytes(8, 'big'))
        # Token balances and supply
        token_registry = getattr(self, '_token_registry', {})
        token_balances = getattr(self, '_token_balances', {})
        for (tid, addr), bal in token_balances.items():
            if bal > 0:
                if not _validate_token_id(tid):
                    raise ValueError(f"invalid token_id in state: {tid!r}")
                trie.set(f"token:{tid}:balance:{addr}", bal.to_bytes(8, 'big'))
        for tid, reg in token_registry.items():
            if not _validate_token_id(tid):
                raise ValueError(f"invalid token_id in state: {tid!r}")
            trie.set(f"token:{tid}:supply",
                     reg["total_minted"].to_bytes(8, 'big'))

    def get_state_proof(self, address: str, key_type: str = "balance") -> dict | None:
        """Generate a Merkle state proof for an address.

        Parameters
        ----------
        address : str
            The account address.
        key_type : str
            Either ``"balance"`` or ``"nonce"``.

        Returns
        -------
        dict or None
            Proof dict with keys: key, value, proof, root.
            None if the key is not in the trie.
        """
        if key_type not in ("balance", "nonce"):
            return None
        trie_key = f"{key_type}:{address}"
        return self._state_trie.get_proof(trie_key)

    def get_token_state_proof(self, token_id: str, address: str) -> dict | None:
        """Generate a Merkle state proof for a token balance."""
        if not _validate_token_id(token_id):
            return None
        trie_key = f"token:{token_id}:balance:{address}"
        return self._state_trie.get_proof(trie_key)

    def get_state_root(self) -> str:
        """Return the current state root as a hex string."""
        return self._state_trie.root().hex()
