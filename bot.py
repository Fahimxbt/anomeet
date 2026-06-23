from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import asyncio
import os
import random
import time

# ========== CONFIG FROM ENVIRONMENT VARIABLES ==========
STRING_SESSION = os.environ.get('STRING_SESSION', '')
API_ID = int(os.environ.get('API_ID', '0'))
API_HASH = os.environ.get('API_HASH', '')
BOT_ID = int(os.environ.get('BOT_ID', '1'))
# ========================================================

if not STRING_SESSION or not API_ID or not API_HASH:
    print("[!] ERROR: Missing environment variables!")
    print("    Required: STRING_SESSION, API_ID, API_HASH")
    exit(1)

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

bot_entity = None
sticker_msg_id = None

# State machine
STATE_IDLE = 'idle'
STATE_FINDING = 'finding'
STATE_MATCHED = 'matched'
STATE_GREETED = 'greeted'
STATE_PROMO_SENT = 'promo_sent'

current_state = STATE_IDLE
state_lock = asyncio.Lock()
match_start_time = 0
last_click_time = 0

# Timeouts
FINDING_TIMEOUT = 20
MATCH_STUCK_TIMEOUT = 60
RECOVERY_INTERVAL = 60
GREET_WAIT = 1
PROMO_WAIT = 4
END_WAIT = 4

# Greetings pool
GREETINGS = ["heyyy", "hiii", "helloo"]


async def safe_send_message(entity, message, retries=3):
    for attempt in range(retries):
        try:
            return await client.send_message(entity, message)
        except FloodWaitError as e:
            print(f"[!] FloodWait: Waiting {e.seconds} seconds...")
            await asyncio.sleep(e.seconds + 2)
        except Exception as e:
            print(f"[!] Send error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(5)
    return None


async def safe_forward_messages(entity, msg_id, from_peer, retries=3):
    for attempt in range(retries):
        try:
            return await client.forward_messages(entity, msg_id, from_peer)
        except FloodWaitError as e:
            print(f"[!] FloodWait: Waiting {e.seconds} seconds...")
            await asyncio.sleep(e.seconds + 2)
        except Exception as e:
            print(f"[!] Forward error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(5)
    return None


async def find_sticker():
    global sticker_msg_id
    try:
        msgs = await client.get_messages('me', limit=50)
        for m in msgs:
            if m.sticker and not sticker_msg_id:
                sticker_msg_id = m.id
                print("[+] Sticker found!")
        if sticker_msg_id:
            return True
    except Exception as e:
        print(f"[!] Find error: {e}")
    print("[!] Send a sticker to Saved Messages first!")
    return False


async def click_start():
    global current_state, last_click_time

    async with state_lock:
        if current_state in (STATE_MATCHED, STATE_GREETED, STATE_PROMO_SENT):
            print(f"[*] In match (state={current_state}), skipping /start")
            return False

        now = time.time()
        if now - last_click_time < 7:
            print(f"[*] Click cooldown active ({now - last_click_time:.1f}s), skipping...")
            return False
        last_click_time = now

        if current_state == STATE_FINDING:
            print("[*] Already finding partner, skipping...")
            return False

        current_state = STATE_FINDING

    # ANTI-SELF-MATCH: staggered random delay based on BOT_ID
    base_delay = (BOT_ID - 1) * 3
    random_delay = random.uniform(0, 4)
    total_delay = base_delay + random_delay
    print(f"[*] Anti-self-match: waiting {total_delay:.1f}s before /start (bot_id={BOT_ID})...")
    await asyncio.sleep(total_delay)

    # Re-check state after delay
    async with state_lock:
        if current_state in (STATE_MATCHED, STATE_GREETED, STATE_PROMO_SENT):
            print(f"[*] State changed to match during delay, aborting /start")
            return False

    print("[→] Sending /start...")
    await safe_send_message(bot_entity, '/start')
    await asyncio.sleep(3)
    return True


async def handle_match():
    global current_state

    # Step 1: Send random greeting after 1s
    async with state_lock:
        if current_state != STATE_MATCHED:
            print(f"[*] Not in match (state={current_state}), aborting handle_match")
            return
        current_state = STATE_GREETED

    print(f"[*] Waiting {GREET_WAIT}s before greeting...")
    await asyncio.sleep(GREET_WAIT)

    async with state_lock:
        if current_state != STATE_GREETED:
            print(f"[*] State changed to {current_state} during greet wait, aborting")
            return

    greeting = random.choice(GREETINGS)
    print(f"[→] Sending greeting: '{greeting}'")
    await safe_send_message(bot_entity, greeting)

    # Step 2: Wait 4s then forward sticker
    print(f"[*] Waiting {PROMO_WAIT}s before sticker...")
    waited = 0
    check_interval = 0.5
    while waited < PROMO_WAIT:
        await asyncio.sleep(check_interval)
        waited += check_interval

        async with state_lock:
            if current_state != STATE_GREETED:
                print(f"[*] State changed to {current_state} during promo wait (early skip after {waited:.1f}s)")
                return

    async with state_lock:
        if current_state != STATE_GREETED:
            print(f"[*] State changed to {current_state} after promo wait, aborting sticker")
            return
        current_state = STATE_PROMO_SENT

    print("[*] Forwarding sticker...")
    try:
        if sticker_msg_id:
            await safe_forward_messages(bot_entity, sticker_msg_id, 'me')
            print("[+] Sticker forwarded!")
        else:
            await safe_send_message(bot_entity, "💜 @chatxbt_bot\nhttps://t.me/chatxbt_bot")
            print("[+] Text promo sent!")
    except Exception as e:
        print(f"[!] Sticker error: {e}")

    # Step 3: Wait 4s then /end
    print(f"[*] Waiting {END_WAIT}s before /end...")
    waited = 0
    while waited < END_WAIT:
        await asyncio.sleep(check_interval)
        waited += check_interval

        async with state_lock:
            if current_state != STATE_PROMO_SENT:
                print(f"[*] State changed to {current_state} during end wait (early skip after {waited:.1f}s)")
                return

    async with state_lock:
        if current_state != STATE_PROMO_SENT:
            print(f"[*] State changed to {current_state} after end wait, aborting /end")
            return

    print("[→] Sending /end...")
    await safe_send_message(bot_entity, '/end')
    await asyncio.sleep(3)

    async with state_lock:
        current_state = STATE_IDLE

    await click_start()


async def handle_finding_timeout():
    global current_state
    await asyncio.sleep(FINDING_TIMEOUT)

    try:
        async with state_lock:
            state = current_state

        if state != STATE_FINDING:
            return

        print(f"[!] Finding timeout! No match after {FINDING_TIMEOUT} seconds.")

        async with state_lock:
            current_state = STATE_IDLE

        await click_start()
    except Exception as e:
        print(f"[!] Finding timeout error: {e}")


async def stuck_watchdog():
    global current_state
    await asyncio.sleep(MATCH_STUCK_TIMEOUT)

    try:
        async with state_lock:
            state = current_state

        if state not in (STATE_MATCHED, STATE_GREETED, STATE_PROMO_SENT):
            return

        elapsed = time.time() - match_start_time
        if elapsed >= MATCH_STUCK_TIMEOUT:
            print(f"[!] MATCH STUCK for {elapsed:.0f}s, forcing /end and next...")

            async with state_lock:
                current_state = STATE_IDLE

            await safe_send_message(bot_entity, '/end')
            await asyncio.sleep(3)
            await click_start()
    except Exception as e:
        print(f"[!] Stuck watchdog error: {e}")


async def recovery_watchdog():
    global current_state
    while True:
        await asyncio.sleep(RECOVERY_INTERVAL)

        try:
            async with state_lock:
                state = current_state

            if state == STATE_IDLE:
                print("[!] Watchdog: Idle state detected, starting...")
                await click_start()
        except Exception as e:
            print(f"[!] Watchdog error: {e}")


@client.on(events.NewMessage(chats='@AnonyMeetBot'))
async def handler(event):
    global current_state, match_start_time

    text = event.text or ''

    if event.out:
        return

    # ========== YOU ARE ALREADY OUT OF CHAT ==========
    if 'You are already out of chat' in text:
        print("[!] Already out of chat — forcing recovery...")
        async with state_lock:
            current_state = STATE_IDLE
        await asyncio.sleep(2)
        await click_start()
        return

    # ========== NOT IN CONVERSATION ==========
    if 'You are not in conversation' in text:
        print("[!] Not in conversation — forcing recovery...")
        async with state_lock:
            current_state = STATE_IDLE
        await asyncio.sleep(2)
        await click_start()
        return

    # ========== PARTNER CLOSED CONVERSATION ==========
    if 'Your partner closed the conversation' in text:
        print("[✓] Partner closed the conversation")
        async with state_lock:
            current_state = STATE_IDLE
        await asyncio.sleep(2)
        await click_start()
        return

    # ========== YOU CLOSED CONVERSATION ==========
    if 'You have closed the conversation' in text:
        print("[✓] We closed the conversation")
        async with state_lock:
            current_state = STATE_IDLE
        await asyncio.sleep(2)
        await click_start()
        return

    # ========== BOT WELCOME / MENU ==========
    if "I'm an anonymous chat bot" in text or "Use the menu or enter the" in text or "Meet strangers" in text:
        print("[*] Bot welcome/menu shown")
        async with state_lock:
            current_state = STATE_IDLE
        await asyncio.sleep(1)
        await click_start()
        return

    # ========== FINDING PARTNER ==========
    if 'Start looking for a partner' in text or 'Finding a partner' in text:
        print("[...] Searching for partner...")
        async with state_lock:
            current_state = STATE_FINDING
        asyncio.create_task(handle_finding_timeout())
        return

    # ========== MATCH STARTED ==========
    if "It's a match" in text or 'Partner found' in text:
        print("[+] Match started!")
        async with state_lock:
            current_state = STATE_MATCHED
            match_start_time = time.time()
        asyncio.create_task(stuck_watchdog())
        asyncio.create_task(handle_match())
        return

    # ========== PARTNER SENT MESSAGE DURING MATCH ==========
    async with state_lock:
        state = current_state

    if state == STATE_MATCHED:
        print("[+] Partner sent message before our greeting!")
        return

    if state == STATE_GREETED:
        print("[+] Partner sent message after greeting, before sticker")
        return

    if state == STATE_PROMO_SENT:
        print("[+] Partner sent message after sticker")
        return


async def main():
    global bot_entity
    await client.start()
    print(f"[*] AnonyMeet Bot (@AnonyMeetBot) started! BOT_ID={BOT_ID}")
    print(f"[*] FINDING_TIMEOUT={FINDING_TIMEOUT}s | MATCH_STUCK_TIMEOUT={MATCH_STUCK_TIMEOUT}s")
    print(f"[*] Flow: match → {GREET_WAIT}s → random greet → {PROMO_WAIT}s → sticker → {END_WAIT}s → /end → /start")
    print("[*] Connected to Telegram successfully!")

    bot_entity = await client.get_entity('@AnonyMeetBot')
    await find_sticker()
    await click_start()

    asyncio.create_task(recovery_watchdog())

    await client.run_until_disconnected()


if __name__ == '__main__':
    while True:
        try:
            with client:
                client.loop.run_until_complete(main())
        except KeyboardInterrupt:
            print("\n[*] Bot stopped by user.")
            break
        except Exception as e:
            print(f"[!] Fatal error: {e}")
            print("[*] Restarting in 10 seconds...")
            time.sleep(10)
