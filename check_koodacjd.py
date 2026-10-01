import sys
sys.path.insert(0, "/home/quillmoney/QuillMoney")
import asyncio
import aiosqlite
from config import DB_PATH


async def main():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, first_name, referral_code, referrals_count, "
            "verified_balance, created_at FROM users WHERE username = ?",
            ("koodacjd",),
        ) as cursor:
            referrer = await cursor.fetchone()
        print("Реферер:", referrer)
        if not referrer:
            print("Не найден")
            return
        referrer_id, _, _, ref_code, _, _, _ = referrer

        print(f"\n=== Все рефералы referred_by={referrer_id}, отсортированы по дате (последние сверху) ===")
        async with db.execute(
            "SELECT user_id, username, first_name, created_at, referred_by FROM users "
            "WHERE referred_by = ? ORDER BY created_at DESC LIMIT 15",
            (referrer_id,),
        ) as cursor:
            recent = await cursor.fetchall()
        for r in recent:
            print(r)

        print("\n=== Логи referral_registered за сегодня для этих user_id ===")
        for r in recent:
            uid = r[0]
            async with db.execute(
                "SELECT action, details, created_at FROM logs WHERE user_id = ? AND action = 'referral_registered'",
                (uid,),
            ) as cursor:
                log = await cursor.fetchone()
            print(uid, log)

        print(f"\n=== Проверка: сколько всего пользователей имеют referral_code = '{ref_code}' (должен быть 1) ===")
        async with db.execute(
            "SELECT user_id, username FROM users WHERE referral_code = ?", (ref_code,)
        ) as cursor:
            matches = await cursor.fetchall()
        print(matches)

        print("\n=== pending_referrals с этим кодом, ожидающие обработки ===")
        async with db.execute(
            "SELECT user_id, referral_code, created_at FROM pending_referrals WHERE referral_code = ? ORDER BY created_at DESC",
            (ref_code,),
        ) as cursor:
            pending = await cursor.fetchall()
        print(f"Всего pending с этим кодом: {len(pending)}")
        for p in pending[:10]:
            print(p)

asyncio.run(main())
