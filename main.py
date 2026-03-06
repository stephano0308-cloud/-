"""텔레그램 주식 분석 봇 - 메인 실행 파일"""

import asyncio
import json
import os
import logging
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import timedelta, timezone

from dotenv import load_dotenv
from telethon import TelegramClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from collector import collect_messages, filter_by_stock, load_config
from analyzer import generate_daily_report

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
MY_CHAT_ID = os.getenv("MY_CHAT_ID", "").strip()
DISABLE_SSL_VERIFY = os.getenv("DISABLE_SSL_VERIFY", "0").strip() == "1"

KST = timezone(timedelta(hours=9))


def validate_env():
    """필수 환경변수가 설정되어 있는지 검증한다."""
    missing = []
    if not BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if API_ID == 0:
        missing.append("TELEGRAM_API_ID")
    if not API_HASH:
        missing.append("TELEGRAM_API_HASH")
    if not MY_CHAT_ID:
        missing.append("MY_CHAT_ID")
    if missing:
        logger.error(f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


def build_ssl_context():
    if DISABLE_SSL_VERIFY:
        logger.warning("SSL 인증서 검증을 비활성화합니다. 신뢰된 네트워크에서만 사용하세요.")
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _create_client():
    return TelegramClient("stock_bot_session", API_ID, API_HASH)


async def send_message(text: str):
    """텔레그램으로 메시지를 전송한다. 4096자 제한에 맞춰 분할 전송."""
    max_len = 4000
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
    ssl_context = build_ssl_context()

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    for chunk in chunks:
        payload = {
            "chat_id": MY_CHAT_ID,
            "text": chunk,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")

        def _send(d=data):
            req = urllib.request.Request(url, data=d, method="POST")
            with urllib.request.urlopen(req, context=ssl_context, timeout=30) as resp:
                return resp.read().decode("utf-8")

        last_err = None
        for attempt in range(3):
            try:
                await asyncio.to_thread(_send)
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.warning(f"텔레그램 전송 시도 {attempt + 1}/3 실패: {e}")
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))

        if last_err is not None:
            logger.error(f"텔레그램 전송 최종 실패: {last_err}")
            raise last_err

        await asyncio.sleep(0.3)


async def daily_job(client: TelegramClient):
    """매일 실행되는 분석 작업"""
    logger.info("일일 분석 작업 시작")

    config = load_config()
    stocks = config["stocks"]
    channels = config["channels"]
    hours = config.get("collect_hours", 24)

    if not await client.is_user_authorized():
        logger.error("Telethon 인증이 필요합니다. 먼저 python auth.py를 실행하세요.")
        return

    logger.info(f"채널 {len(channels)}개에서 최근 {hours}시간 메시지 수집 중...")
    messages = await collect_messages(client, channels, hours)
    logger.info(f"총 {len(messages)}개 메시지 수집 완료")

    stock_data = filter_by_stock(messages, stocks)

    logger.info("종목별 분석 중...")
    reports = generate_daily_report(stock_data)

    header = "🗓 일일 주식 분석 리포트\n" + "=" * 30
    await send_message(header)

    for report in reports:
        await send_message(report)
        await asyncio.sleep(1)

    footer = "\n✅ 분석 완료. 이 리포트는 AI가 자동 생성한 참고자료입니다."
    await send_message(footer)
    logger.info("일일 분석 전송 완료")


async def manual_run():
    client = _create_client()
    await client.start()
    try:
        await daily_job(client)
    finally:
        await client.disconnect()


async def run_scheduler():
    validate_env()

    config = load_config()
    hour = config.get("schedule_hour", 9)
    minute = config.get("schedule_minute", 0)

    client = _create_client()
    await client.start()
    logger.info("Telethon 클라이언트 연결 완료")

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(daily_job, "cron", hour=hour, minute=minute, args=[client])
    scheduler.start()
    logger.info(f"스케줄러 시작 - 매일 {hour:02d}:{minute:02d} KST에 분석 실행")

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("봇 종료 중...")
        scheduler.shutdown()
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        asyncio.run(manual_run())
    else:
        asyncio.run(run_scheduler())
