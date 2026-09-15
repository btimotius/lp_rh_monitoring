"""
ABI minimal - hanya fungsi & event yang benar-benar dipakai.
Sengaja tidak pakai ABI lengkap resmi (yang ribuan baris) supaya gampang di-review
dan risiko salah decode lebih kecil (permukaan lebih kecil = lebih gampang diverifikasi).
"""

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
]

V3_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# NonfungiblePositionManager (V3) - subset
V3_NFPM_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "index", "type": "uint256"}],
     "name": "tokenOfOwnerByIndex", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [{"name": "tokenId", "type": "uint256"}], "name": "positions",
     "outputs": [
         {"name": "nonce", "type": "uint96"},
         {"name": "operator", "type": "address"},
         {"name": "token0", "type": "address"},
         {"name": "token1", "type": "address"},
         {"name": "fee", "type": "uint24"},
         {"name": "tickLower", "type": "int24"},
         {"name": "tickUpper", "type": "int24"},
         {"name": "liquidity", "type": "uint128"},
         {"name": "feeGrowthInside0LastX128", "type": "uint256"},
         {"name": "feeGrowthInside1LastX128", "type": "uint256"},
         {"name": "tokensOwed0", "type": "uint128"},
         {"name": "tokensOwed1", "type": "uint128"},
     ], "stateMutability": "view", "type": "function"},
    {
        "inputs": [{
            "components": [
                {"name": "tokenId", "type": "uint256"},
                {"name": "recipient", "type": "address"},
                {"name": "amount0Max", "type": "uint128"},
                {"name": "amount1Max", "type": "uint128"},
            ],
            "name": "params", "type": "tuple",
        }],
        "name": "collect",
        "outputs": [{"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"}],
        "stateMutability": "payable", "type": "function",
    },
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "tokenId", "type": "uint256"},
        {"indexed": False, "name": "recipient", "type": "address"},
        {"indexed": False, "name": "amount0", "type": "uint256"},
        {"indexed": False, "name": "amount1", "type": "uint256"},
    ], "name": "Collect", "type": "event"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "tokenId", "type": "uint256"},
        {"indexed": False, "name": "liquidity", "type": "uint128"},
        {"indexed": False, "name": "amount0", "type": "uint256"},
        {"indexed": False, "name": "amount1", "type": "uint256"},
    ], "name": "IncreaseLiquidity", "type": "event"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "tokenId", "type": "uint256"},
        {"indexed": False, "name": "liquidity", "type": "uint128"},
        {"indexed": False, "name": "amount0", "type": "uint256"},
        {"indexed": False, "name": "amount1", "type": "uint256"},
    ], "name": "DecreaseLiquidity", "type": "event"},
]

# ERC721 generic (dipakai untuk V4 PositionManager: balanceOf/ownerOf/Transfer event -
# V4 PositionManager TIDAK enumerable, jadi tokenOfOwnerByIndex sengaja tidak disertakan)
ERC721_MIN_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [{"name": "tokenId", "type": "uint256"}], "name": "ownerOf",
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": True, "name": "tokenId", "type": "uint256"},
    ], "name": "Transfer", "type": "event"},
]

# Uniswap V4 PositionManager (periphery) - subset
# Sumber: docs.uniswap.org/contracts/v4/reference/periphery/PositionManager
V4_POSITION_MANAGER_ABI = ERC721_MIN_ABI + [
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "getPoolAndPositionInfo",
        "outputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
                "name": "poolKey", "type": "tuple",
            },
            {"name": "info", "type": "uint256"},  # PositionInfo packed - didekode manual, lihat uniswap_math.py
        ],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "getPositionLiquidity",
        "outputs": [{"name": "liquidity", "type": "uint128"}],
        "stateMutability": "view", "type": "function",
    },
]

# Uniswap V4 StateView (lens) - subset
# Sumber: docs.uniswap.org/contracts/v4/reference/periphery/lens/StateView
V4_STATE_VIEW_ABI = [
    {
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "name": "getSlot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "protocolFee", "type": "uint24"},
            {"name": "lpFee", "type": "uint24"},
        ],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "owner", "type": "address"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "salt", "type": "bytes32"},
        ],
        "name": "getPositionInfo",
        "outputs": [
            {"name": "liquidity", "type": "uint128"},
            {"name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"name": "feeGrowthInside1LastX128", "type": "uint256"},
        ],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
        ],
        "name": "getFeeGrowthInside",
        "outputs": [
            {"name": "feeGrowthInside0X128", "type": "uint256"},
            {"name": "feeGrowthInside1X128", "type": "uint256"},
        ],
        "stateMutability": "view", "type": "function",
    },
]


# --- Uniswap V4 PoolManager events (verified from v4-core IPoolManager.sol) ---
# event ModifyLiquidity(PoolId indexed id, address indexed sender, int24 tickLower,
#                       int24 tickUpper, int256 liquidityDelta, bytes32 salt);
# event Swap(PoolId indexed id, address indexed sender, int128 amount0, int128 amount1,
#            uint160 sqrtPriceX96, uint128 liquidity, int24 tick, uint24 fee);
# CATATAN: `salt` TIDAK indexed, jadi filter tokenId harus dilakukan di sisi klien
# setelah log diambil per-poolId (poolId indexed, jadi itu yang dipakai memfilter).
V4_POOL_MANAGER_ABI = [
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "id", "type": "bytes32"},
        {"indexed": True, "name": "sender", "type": "address"},
        {"indexed": False, "name": "tickLower", "type": "int24"},
        {"indexed": False, "name": "tickUpper", "type": "int24"},
        {"indexed": False, "name": "liquidityDelta", "type": "int256"},
        {"indexed": False, "name": "salt", "type": "bytes32"},
    ], "name": "ModifyLiquidity", "type": "event"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "id", "type": "bytes32"},
        {"indexed": True, "name": "sender", "type": "address"},
        {"indexed": False, "name": "amount0", "type": "int128"},
        {"indexed": False, "name": "amount1", "type": "int128"},
        {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
        {"indexed": False, "name": "liquidity", "type": "uint128"},
        {"indexed": False, "name": "tick", "type": "int24"},
        {"indexed": False, "name": "fee", "type": "uint24"},
    ], "name": "Swap", "type": "event"},
]

ERC20_TRANSFER_ABI = [
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ], "name": "Transfer", "type": "event"},
]
