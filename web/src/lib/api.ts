import type {
  NodeInfo,
  Block,
  BlocksResponse,
  Transaction,
  TxsResponse,
  ValidatorsResponse,
  PoolResponse,
  Wallet,
  WalletsResponse,
  StakeInfo,
  StakesResponse,
  EpochInfo,
  SlashingResponse,
  BalanceInfo,
  SupplyInfo,
  FeeInfo,
} from "./types";

export class QBitAPI {
  private baseUrl: string;
  private token: string;

  constructor(baseUrl: string, token: string = "") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  setConnection(baseUrl: string, token: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    if (body) headers["Content-Type"] = "application/json";

    const res = await fetch(`${this.baseUrl}/api/v1${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    const json = await res.json();
    if (json.error) throw new Error(json.error.message || json.error);
    return json.data ?? json;
  }

  private get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  // Public endpoints
  getInfo() {
    return this.get<NodeInfo>("/info");
  }
  getHealth() {
    return this.get<{ status: string }>("/health");
  }
  getBlocks(page = 1, limit = 20) {
    return this.get<BlocksResponse>(`/blocks?page=${page}&limit=${limit}`);
  }
  getBlock(index: number) {
    return this.get<Block>(`/blocks/${index}`);
  }
  getLatestBlock() {
    return this.get<Block>("/blocks/latest");
  }
  getBlockByHash(hash: string) {
    return this.get<Block>(`/blocks/hash/${hash}`);
  }
  getTx(txId: string) {
    return this.get<Transaction>(`/txs/${txId}`);
  }
  getTxsBySender(addr: string, page = 1) {
    return this.get<TxsResponse>(`/txs/sender/${addr}?page=${page}`);
  }
  getAddress(addr: string) {
    return this.get<Record<string, unknown>>(`/address/${addr}`);
  }
  getValidators() {
    return this.get<ValidatorsResponse>("/validators");
  }
  getPool() {
    return this.get<PoolResponse>("/pool");
  }
  getStakes(validator?: string) {
    return validator
      ? this.get<StakeInfo>(`/stakes/${validator}`)
      : this.get<StakesResponse>("/stakes");
  }
  getCurrentEpoch() {
    return this.get<EpochInfo>("/epochs/current");
  }
  getSlashingEvents() {
    return this.get<SlashingResponse>("/slashing-events");
  }
  getNotarization(hash: string) {
    return this.get<Record<string, unknown>>(`/notarizations/${hash}`);
  }

  // Financial endpoints
  getBalance(addr: string) {
    return this.get<BalanceInfo>(`/balance/${addr}`);
  }
  getSupply() {
    return this.get<SupplyInfo>("/supply");
  }
  getFeeInfo() {
    return this.get<FeeInfo>("/fee");
  }

  // Protected endpoints
  verify(documentHash: string) {
    return this.post<{ verified: boolean; proof: unknown }>("/verify", {
      document_hash: documentHash,
    });
  }
  createWallet() {
    return this.post<Wallet>("/wallets", {});
  }
  listWallets() {
    return this.get<WalletsResponse>("/wallets");
  }
  notarize(wallet: string, hash: string, metadata: string) {
    return this.post<{ tx_id: string }>("/notarize", {
      wallet_address: wallet,
      document_hash: hash,
      metadata,
    });
  }
  stake(wallet: string, validator: string, amount: number) {
    return this.post<{ tx_id: string }>("/stake", {
      wallet_address: wallet,
      validator_address: validator,
      amount,
    });
  }
  delegate(wallet: string, validator: string, amount: number) {
    return this.post<{ tx_id: string }>("/delegate", {
      wallet_address: wallet,
      validator_address: validator,
      amount,
    });
  }
  unstake(wallet: string, validator: string, amount: number) {
    return this.post<{ tx_id: string }>("/unstake", {
      wallet_address: wallet,
      validator_address: validator,
      amount,
    });
  }
  transfer(wallet: string, to: string, amount: number, memo?: string) {
    return this.post<{ tx_id: string }>("/transfer", {
      wallet_address: wallet,
      to_address: to,
      amount,
      ...(memo ? { memo } : {}),
    });
  }
}

// Singleton instance
let apiInstance: QBitAPI | null = null;

export function getApi(): QBitAPI {
  if (!apiInstance) {
    const savedUrl =
      typeof window !== "undefined"
        ? localStorage.getItem("qbit_api_url") || ""
        : "";
    const savedToken =
      typeof window !== "undefined"
        ? localStorage.getItem("qbit_auth_token") || ""
        : "";
    apiInstance = new QBitAPI(savedUrl, savedToken);
  }
  return apiInstance;
}
