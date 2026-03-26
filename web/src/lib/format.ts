/**
 * Format a raw qubits amount (integer) as a human-readable QBIT string.
 * 1 QBIT = 1,000,000,000 qubits (10^9).
 */
export function formatQBIT(qubits: number): string {
  const qbit = qubits / 1_000_000_000;
  return (
    qbit.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 9,
    }) + " QBIT"
  );
}

/**
 * Parse a user-entered QBIT string into raw qubits (integer).
 * Returns 0 for invalid or negative input.
 */
export function parseQBIT(input: string): number {
  const num = parseFloat(input);
  if (isNaN(num) || num < 0) return 0;
  return Math.round(num * 1_000_000_000);
}

/** Standard transaction fee in qubits */
export const TX_FEE_QUBITS = 1_000_000; // 0.001 QBIT

/** Format the standard fee for display */
export const TX_FEE_DISPLAY = "0.001 QBIT";
