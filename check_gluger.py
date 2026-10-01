import sys
sys.path.insert(0, "/home/quillmoney/QuillMoney")
import asyncio
import aiosqlite
from config import DB_PATH


async def main():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, first_name, referral_code, referrals_count, "
            "verified_balance, unverified_balance, created_at FROM users WHERE username = ?",
            ("GlugerOficion",),
        ) as cursor:
            referrer = await cursor.fetchone()
        print("Реферер:", referrer)
        if not referrer:
            print("Не найден")
            return
        referrer_id, _, _, ref_code, _, _, _, _ = referrer

        print(f"\n=== Приглашённые (referred_by = {referrer_id}) ===")
        async with db.execute(
            "SELECT user_id, username, first_name, created_at FROM users "
            "WHERE referred_by = ? ORDER BY created_at",
            (referrer_id,),
        ) as cursor:
            referred = await cursor.fetchall()
        print(f"Всего рефералов в БД: {len(referred)}")
        for r in referred:
            print(r)

        active = 0
        for r in referred:
            uid = r[0]
            async with db.execute("SELECT COUNT(*) FROM logs WHERE user_id = ?", (uid,)) as cursor:
                log_count = (await cursor.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM user_tasks WHERE user_id = ?", (uid,)) as cursor:
                task_count = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT action, details, created_at FROM logs WHERE user_id = ? ORDER BY created_at", (uid,)
            ) as cursor:
                all_logs = await cursor.fetchall()
            is_active = log_count > 3
            if is_active:
                active += 1
            print(f"\nuser_id={uid} logs={log_count} tasks={task_count} active={'YES' if is_active else 'no'}")
            for lg in all_logs:
                print("   ", lg)

        if referred:
            print(f"\nАктивных после регистрации: {active}/{len(referred)} ({100*active/len(referred):.0f}%)")

        print(f"\n=== pending_referrals с кодом реферера '{ref_code}' ===")
        async with db.execute(
            "SELECT user_id, referral_code, created_at FROM pending_referrals WHERE referral_code = ? ORDER BY created_at",
            (ref_code,),
        ) as cursor:
            pending = await cursor.fetchall()
        print(f"Всего pending: {len(pending)}")
        for p in pending:
            print(p)

asyncio.run(main())
