async def m001_initial(db):
    await db.execute(
        f"""
        CREATE TABLE boltz.submarineswap (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            payment_hash TEXT NOT NULL,
            amount {db.big_int} NOT NULL,
            status TEXT NOT NULL,
            boltz_id TEXT NOT NULL,
            refund_address TEXT NOT NULL,
            refund_privkey TEXT NOT NULL,
            expected_amount {db.big_int} NOT NULL,
            timeout_block_height INT NOT NULL,
            address TEXT NOT NULL,
            bip21 TEXT NOT NULL,
            redeem_script TEXT NOT NULL,
            time TIMESTAMP NOT NULL DEFAULT """
        + db.timestamp_now
        + """
        );
    """
    )
    await db.execute(
        f"""
        CREATE TABLE boltz.reverse_submarineswap (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            onchain_address TEXT NOT NULL,
            amount {db.big_int} NOT NULL,
            instant_settlement BOOLEAN NOT NULL,
            status TEXT NOT NULL,
            boltz_id TEXT NOT NULL,
            timeout_block_height INT NOT NULL,
            redeem_script TEXT NOT NULL,
            preimage TEXT NOT NULL,
            claim_privkey TEXT NOT NULL,
            lockup_address TEXT NOT NULL,
            invoice TEXT NOT NULL,
            onchain_amount {db.big_int} NOT NULL,
            time TIMESTAMP NOT NULL DEFAULT """
        + db.timestamp_now
        + """
        );
    """
    )


async def m002_auto_swaps(db):
    await db.execute(
        f"""
        CREATE TABLE boltz.auto_reverse_submarineswap (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            onchain_address TEXT NOT NULL,
            amount {db.big_int} NOT NULL,
            balance {db.big_int} NOT NULL,
            instant_settlement BOOLEAN NOT NULL,
            time TIMESTAMP NOT NULL DEFAULT """
        + db.timestamp_now
        + """
        );
    """
    )


async def m003_custom_feerate(db):
    await db.execute(
        "ALTER TABLE boltz.auto_reverse_submarineswap ADD COLUMN feerate_limit INT NULL"
    )

    await db.execute(
        "ALTER TABLE boltz.reverse_submarineswap ADD COLUMN feerate_value INT NULL"
    )
    await db.execute(
        "ALTER TABLE boltz.reverse_submarineswap "
        "ADD COLUMN feerate BOOLEAN NOT NULL DEFAULT false"
    )

    await db.execute(
        "ALTER TABLE boltz.submarineswap ADD COLUMN feerate_value INT NULL"
    )
    await db.execute(
        "ALTER TABLE boltz.submarineswap "
        "ADD COLUMN feerate BOOLEAN NOT NULL DEFAULT false"
    )


async def m004_add_settings_counter_direction_asset(db):
    """
    Add extension settings table
    add count column for auto swaps
    add direction column for swaps
    add asset column for swaps
    add blind key column for swaps
    """
    # Add settings table
    await db.execute(
        """
        CREATE TABLE boltz.settings (
            boltz_network TEXT NOT NULL,
            boltz_url TEXT NOT NULL,
            boltz_mempool_space_url TEXT NOT NULL,
            boltz_network_liquid TEXT NOT NULL,
            boltz_mempool_space_liquid_url TEXT NOT NULL,
        );
        """
    )

    # Add count column
    await db.execute(
        "ALTER TABLE boltz.auto_reverse_submarineswap "
        "ADD COLUMN count INT DEFAULT 0"
    )

    # Add direction column
    await db.execute(
        "ALTER TABLE boltz.reverse_submarineswap "
        "ADD COLUMN direction TEXT NOT NULL DEFAULT 'send'"
    )
    await db.execute(
        "ALTER TABLE boltz.submarineswap "
        "ADD COLUMN direction TEXT NOT NULL DEFAULT 'receive'"
    )

    # Add asset column
    await db.execute(
        "ALTER TABLE boltz.auto_reverse_submarineswap "
        "ADD COLUMN asset TEXT NOT NULL DEFAULT 'BTC/BTC'"
    )
    await db.execute(
        "ALTER TABLE boltz.reverse_submarineswap "
        "ADD COLUMN asset TEXT NOT NULL DEFAULT 'BTC/BTC'"
    )
    await db.execute(
        "ALTER TABLE boltz.submarineswap "
        "ADD COLUMN asset TEXT NOT NULL DEFAULT 'BTC/BTC'"
    )

    # add blind key column
    await db.execute(
        "ALTER TABLE boltz.reverse_submarineswap "
        "ADD COLUMN blinding_key TEXT NULL"
    )
    await db.execute(
        "ALTER TABLE boltz.submarineswap "
        "ADD COLUMN blinding_key TEXT NULL"
    )
