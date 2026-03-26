export interface BlockHeader {
  index: number;
  hash: string;
  prevHash: string;
  merkleRoot: string;
  validator: string;
  timestamp: number;
  signature: string;
}

export interface Block extends BlockHeader {
  transactions: Transaction[];
}

export interface Transaction {
  tx_id: string;
  type: string;
  from: string;
  to: string;
  timestamp: number;
  nonce: number;
  chainId: string;
  payload: Record<string, unknown>;
  signature: string;
  sender_pubkey: string;
}

export interface NodeInfo {
  version: string;
  chain_height: number;
  pending_txs: number;
  peers: number;
  validator: string;
  validators: string[];
  registered_validators: string[];
  wallets: number;
}

export interface Wallet {
  address: string;
  signing_pk: string;
  encryption_pk: string;
}

export interface StakeInfo {
  validator_address: string;
  total_stake: number;
  stakers: Record<string, number>;
}

export interface EpochInfo {
  epoch: number;
  validators: Array<[string, number]>;
}

export interface SlashingEvent {
  validator: string;
  evidence_tx_id: string;
  amount_slashed: number;
  block_index: number;
}

export interface BlocksResponse {
  blocks: BlockHeader[];
  total: number;
  page: number;
  limit: number;
}

export interface TxsResponse {
  transactions: Transaction[];
  total: number;
}

export interface ValidatorsResponse {
  validators: string[];
  total: number;
}

export interface PoolResponse {
  count: number;
  by_type: Record<string, number>;
}

export interface WalletsResponse {
  wallets: string[];
}

export interface SlashingResponse {
  events: SlashingEvent[];
}

export interface StakesResponse {
  stakes: Record<string, StakeInfo>;
}

export interface BalanceInfo {
  address: string;
  balance: number; // qubits
  formatted: string; // "123.45 QBIT"
}

export interface SupplyInfo {
  total_minted: number;
  total_burned: number;
  circulating: number;
  staked: number;
  max_supply: number;
}
