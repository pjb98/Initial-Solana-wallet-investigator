# Solana developer-wallet investigation rules

When given a token mint and suspected developer wallet:

1. Treat the developer attribution as unverified until supported by the
   creation transaction, Pump.fun creator metadata, public disclosure,
   or equivalent primary evidence.

2. Retrieve complete paginated transaction histories using Helius.

3. Focus only on:
   - initial wallet funding
   - token creation
   - developer purchases
   - transfers to side wallets
   - side-wallet sales
   - SOL and stablecoin proceeds
   - proceeds returning to related wallets
   - liquidity actions
   - exchange deposits
   - net token accumulation or distribution

4. Do not call a transfer a sale unless a swap or exchange deposit is shown.

5. Label conclusions:
   - Verified
   - Likely
   - Possible
   - Unknown
   - Contradicted

6. For every related-wallet attribution, report:
   - evidence connecting the wallet
   - alternative explanations
   - confidence level

7. Detect suspected buyback theatre:
   developer buys token
   -> transfers token to another wallet
   -> recipient sells token
   -> proceeds return to the developer cluster or common controller

8. Produce:
   - reports/latest.md
   - reports/latest.json
   - reports/transactions.csv
   - reports/wallet_graph.csv

9. Never report a project as fraudulent solely from behavioral patterns.

