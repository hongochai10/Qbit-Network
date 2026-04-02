"""Peer reputation scoring — track peer behavior and ban misbehaving peers."""
import logging
import time

logger = logging.getLogger("qbit_network.reputation")


class PeerReputation:
    """Score-based peer reputation tracker.

    Each peer starts at DEFAULT_SCORE. Events (valid block, invalid tx, etc.)
    adjust the score. Peers whose score drops below BAN_THRESHOLD are banned
    and auto-disconnected. Scores decay toward DEFAULT_SCORE over time so that
    old events do not permanently affect reputation.
    """

    EVENTS = {
        "valid_block": +10,
        "valid_tx": +1,
        "invalid_block": -50,
        "invalid_tx": -10,
        "state_root_mismatch": -100,  # Byzantine: forged state root (RS-2)
        "auth_failed": -100,
        "timeout": -5,
        "rate_limited": -20,
        "protocol_error": -30,
    }

    DEFAULT_SCORE = 100
    BAN_THRESHOLD = -100
    DECAY_RATE = 0.99  # multiplicative decay toward DEFAULT_SCORE per decay() call

    def __init__(self):
        self._scores: dict[str, float] = {}
        self._banned: set[str] = set()
        self._last_event: dict[str, float] = {}  # peer_id -> monotonic timestamp

    def record(self, peer_id: str, event: str) -> None:
        """Record a reputation event for a peer.

        Args:
            peer_id: Unique peer identifier (e.g. host:port or address).
            event: Event name from EVENTS dict.

        Raises:
            ValueError: If event is not a recognized event type.
        """
        if not isinstance(peer_id, str) or not peer_id:
            return
        if not isinstance(event, str):
            return
        if event not in self.EVENTS:
            raise ValueError(f"unknown reputation event: {event}")

        delta = self.EVENTS[event]
        current = self._scores.get(peer_id, self.DEFAULT_SCORE)
        new_score = current + delta
        self._scores[peer_id] = new_score
        self._last_event[peer_id] = time.monotonic()

        if new_score <= self.BAN_THRESHOLD and peer_id not in self._banned:
            self._banned.add(peer_id)
            logger.warning(
                f"Peer {peer_id} BANNED: score={new_score:.1f} "
                f"(threshold={self.BAN_THRESHOLD})")

        logger.debug(
            f"Reputation: {peer_id} event={event} delta={delta:+d} "
            f"score={new_score:.1f}")

    def get_score(self, peer_id: str) -> float:
        """Return the current reputation score for a peer.

        Returns DEFAULT_SCORE for unknown peers.
        """
        if not isinstance(peer_id, str) or not peer_id:
            return self.DEFAULT_SCORE
        return self._scores.get(peer_id, self.DEFAULT_SCORE)

    def is_banned(self, peer_id: str) -> bool:
        """Check if a peer is currently banned."""
        if not isinstance(peer_id, str) or not peer_id:
            return False
        return peer_id in self._banned

    def unban(self, peer_id: str) -> None:
        """Manually unban a peer and reset their score to DEFAULT_SCORE."""
        if not isinstance(peer_id, str) or not peer_id:
            return
        self._banned.discard(peer_id)
        self._scores[peer_id] = self.DEFAULT_SCORE
        logger.info(f"Peer {peer_id} unbanned, score reset to {self.DEFAULT_SCORE}")

    def decay(self) -> None:
        """Apply multiplicative decay to all scores, moving them toward DEFAULT_SCORE.

        Should be called periodically (e.g., once per minute from a cleanup loop).
        Scores decay as: new = DEFAULT_SCORE + (score - DEFAULT_SCORE) * DECAY_RATE
        This ensures old events fade over time while idle peers converge to DEFAULT_SCORE.
        """
        to_remove = []
        for peer_id, score in self._scores.items():
            new_score = self.DEFAULT_SCORE + (score - self.DEFAULT_SCORE) * self.DECAY_RATE
            # If score is very close to DEFAULT_SCORE after decay and peer is not
            # banned, we can clean up the entry
            if abs(new_score - self.DEFAULT_SCORE) < 0.01 and peer_id not in self._banned:
                to_remove.append(peer_id)
            else:
                self._scores[peer_id] = new_score
                # Check if decayed score crosses back above ban threshold
                # (does NOT auto-unban -- only unban() does that)

        for peer_id in to_remove:
            self._scores.pop(peer_id, None)
            self._last_event.pop(peer_id, None)

    def get_all_scores(self) -> dict[str, float]:
        """Return a copy of all tracked peer scores."""
        return dict(self._scores)

    def get_banned_peers(self) -> set[str]:
        """Return a copy of the banned peer set."""
        return set(self._banned)

    def peer_count(self) -> int:
        """Return the number of tracked peers."""
        return len(self._scores)
