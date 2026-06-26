"""
주간 자동 매매 전략: 월-금 자동 매매

기능:
  - 월요일 9:30부터 금요일 14:00까지 매시간 삼성전자 1주 매수 (시장가)
  - 금요일 15:10에 보유한 모든 삼성전자 매도 (종가 시장가)

사용법:
  # 모의투자
  uv run python examples/weekly_autotrader.py --env vps
  
  # 실전투자
  uv run python examples/weekly_autotrader.py --env prod
"""

import argparse
import sys
import os
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
import logging

import pandas as pd

# strategy_builder 경로 추가 (kis_auth 모듈용)
strategy_builder_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, strategy_builder_root)

# 프로젝트 루트 경로 추가 (examples_user 모듈용)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

import kis_auth as ka
from examples_user.domestic_stock.domestic_stock_functions import order_cash, inquire_balance

# 설정
STOCK_CODE = "005930"  # 삼성전자
STOCK_NAME = "삼성전자"
ORDER_QUANTITY = "1"   # 1주씩 주문
KST = ZoneInfo("Asia/Seoul")

# 글로벌 상태
trading_state = {
    "positions": 0,  # 현재 보유 주식 수
    "buy_times": [],  # 매수 시간 기록
    "is_running": False,
    "is_paper": True  # 기본값은 모의투자 모드
}


def log(message: str):
    """시간 포함 로그 출력"""
    timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def _extract_order_message(result) -> str:
    """주문 응답에서 메시지를 안전하게 추출"""
    if result is None:
        return ""

    if isinstance(result, pd.DataFrame):
        if result.empty:
            return ""
        row = result.iloc[0]
        msg = row.get("msg") if "msg" in row.index else ""
        if isinstance(msg, list):
            msg = msg[0] if msg else ""
        if isinstance(msg, str):
            return msg.strip()
        if pd.notna(msg):
            return str(msg).strip()
        return ""

    return ""


def _extract_order_id(result) -> str:
    """주문 응답에서 주문번호를 안전하게 추출"""
    if result is None:
        return ""

    if isinstance(result, pd.DataFrame):
        if result.empty:
            return ""
        row = result.iloc[0]
        for key in ("ODNO", "odno", "ORDER_NO", "order_no"):
            if key in row.index:
                value = row.get(key)
                if isinstance(value, list):
                    value = value[0] if value else ""
                if isinstance(value, str):
                    value = value.strip()
                    if value:
                        return value
                elif pd.notna(value):
                    return str(value).strip()
        return ""

    return ""


def is_order_success(result) -> bool:
    """메시지 텍스트가 비어 있어도 주문번호/시간이 있으면 성공으로 간주"""
    if result is None:
        return False

    if isinstance(result, pd.DataFrame):
        if result.empty:
            return False

        msg = _extract_order_message(result)
        if msg:
            lowered = msg.lower()
            if any(token in lowered for token in ("success", "정상", "접수", "완료", "처리")):
                return True

        order_id = _extract_order_id(result)
        if order_id:
            return True

        row = result.iloc[0]
        for key in ("ORD_TMD", "ord_tmd", "KRX_FWDG_ORD_ORGNO", "krx_fwdg_ord_orgno"):
            if key in row.index and pd.notna(row.get(key)):
                return True

    return False


def _refresh_position_from_account() -> int:
    """계좌 상태를 다시 조회해 위치를 동기화"""
    try:
        return get_positions()
    except Exception:
        return trading_state.get("positions", 0)


def get_positions():
    """계좌의 삼성전자 보유 수량 조회"""
    try:
        ka.auth(svr="vps", product="01")
        env = ka.getTREnv()
        env_dv = "demo" if trading_state.get("is_paper", True) else "real"

        balance_df, _ = inquire_balance(
            env_dv=env_dv,
            cano=env.my_acct,
            acnt_prdt_cd=env.my_prod,
            afhr_flpr_yn="N",
            inqr_dvsn="01",
            unpr_dvsn="01",
            fund_sttl_icld_yn="N",
            fncg_amt_auto_rdpt_yn="N",
            prcs_dvsn="00"
        )

        if balance_df is not None and not balance_df.empty:
            samsung_row = balance_df[balance_df['pdno'] == STOCK_CODE]
            if not samsung_row.empty:
                raw_qty = samsung_row['hldg_qty'].iloc[0]
                qty_value = pd.to_numeric(raw_qty, errors='coerce')
                qty = int(qty_value) if pd.notna(qty_value) else 0
                trading_state["positions"] = qty
                log(f"현재 보유: {qty}주")
                return qty

        log("보유 종목 없음")
        return 0

    except Exception as e:
        log(f"보유 수량 조회 실패: {e}")
        return 0


def buy_one():
    """1주 매수 (시장가)"""
    try:
        ka.auth(svr="vps", product="01")
        env = ka.getTREnv()

        log(f"매수 주문 시작: 1주")
        
        env_dv = "demo" if trading_state.get("is_paper", True) else "real"

        result = order_cash(
            env_dv=env_dv,
            ord_dv="buy",
            cano=env.my_acct,
            acnt_prdt_cd=env.my_prod,
            pdno=STOCK_CODE,
            ord_dvsn="01",        # 시장가
            ord_qty=ORDER_QUANTITY,
            ord_unpr="0",         # 시장가는 0
            excg_id_dvsn_cd="KRX"
        )
        
        # 결과 확인
        if result is not None and not result.empty:
            msg = _extract_order_message(result)
            if is_order_success(result):
                trading_state["positions"] += 1
                trading_state["buy_times"].append(datetime.now(KST))
                time.sleep(1)
                confirmed_qty = _refresh_position_from_account()
                if confirmed_qty >= 0:
                    trading_state["positions"] = max(trading_state["positions"], confirmed_qty)
                order_id = _extract_order_id(result)
                if order_id:
                    log(f"✓ 매수 완료! 주문번호: {order_id} / 누적: {trading_state['positions']}주")
                else:
                    log(f"✓ 매수 완료! 메시지가 비어 있었지만 주문 접수된 것으로 확인됨 / 누적: {trading_state['positions']}주")
            else:
                log(f"✗ 매수 실패: {msg or '응답 메시지 없음'}")
        else:
            log("✗ 매수 응답 없음")
        
        time.sleep(1)
        
    except Exception as e:
        log(f"✗ 매수 오류: {e}")


def sell_one():
    """1주 매도 (시장가)"""
    try:
        if trading_state["positions"] <= 0:
            log("매도할 주식 없음")
            return

        ka.auth(svr="vps", product="01")
        env = ka.getTREnv()
        qty_to_sell = 1
        
        log(f"매도 주문 시작: {qty_to_sell}주")
        
        env_dv = "demo" if trading_state.get("is_paper", True) else "real"

        result = order_cash(
            env_dv=env_dv,
            ord_dv="sell",
            cano=env.my_acct,
            acnt_prdt_cd=env.my_prod,
            pdno=STOCK_CODE,
            ord_dvsn="01",        # 시장가 (종가)
            ord_qty=str(qty_to_sell),
            ord_unpr="0",
            excg_id_dvsn_cd="KRX"
        )
        
        # 결과 확인
        if result is not None and not result.empty:
            msg = _extract_order_message(result)
            if is_order_success(result):
                log(f"✓ 매도 완료! 판매: {qty_to_sell}주")
                trading_state["positions"] = max(0, trading_state["positions"] - qty_to_sell)
                time.sleep(1)
                confirmed_qty = _refresh_position_from_account()
                if confirmed_qty >= 0:
                    trading_state["positions"] = confirmed_qty
                if trading_state["positions"] == 0:
                    trading_state["buy_times"] = []
            else:
                log(f"✗ 매도 실패: {msg or '응답 메시지 없음'}")
        else:
            log("✗ 매도 응답 없음")
        
        time.sleep(1)
        
    except Exception as e:
        log(f"✗ 매도 오류: {e}")


def schedule_trades():
    """매매 스케줄 실행 (테스트용 - 수동 시간)"""
    log("=" * 60)
    log("주간 자동 매매 시작")
    log(f"종목: {STOCK_NAME} ({STOCK_CODE})")
    log(f"환경: {'모의투자' if trading_state.get('is_paper', True) else '실전투자'}")
    log("일정: 평일 09:00 매수, 09:02 매도, 09:04 매수 ... 4분 간격 매수/매도 반복")
    log("=" * 60)

    trading_state["is_running"] = True


def should_buy():
    """현재 시간에 매수해야 하는지 확인"""
    now = datetime.now(KST)
    weekday = now.weekday()  # 0=월, 4=금, 5=토, 6=일
    hour = now.hour
    minute = now.minute

    if weekday >= 5:
        return False

    if hour < 9 or hour > 15:
        return False

    if hour == 15 and minute > 0:
        return False

    return minute % 4 == 0


def should_sell():
    """현재 시간에 매도해야 하는지 확인"""
    now = datetime.now(KST)
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    if weekday >= 5:
        return False

    if hour < 9 or hour > 15:
        return False

    if hour == 9 and minute < 2:
        return False

    if hour == 15 and minute > 0:
        return False

    return minute % 4 == 2


def run_manual_mode():
    """수동 입력 모드 (테스트용)"""
    print("\n[수동 모드]")
    print("명령어: buy, sell, status, exit")
    
    while True:
        cmd = input("\n명령 입력> ").strip().lower()
        
        if cmd == "buy":
            buy_one()
        elif cmd == "sell":
            sell_one()
        elif cmd == "status":
            print(f"보유: {trading_state['positions']}주")
            print(f"매수 시간: {[t.strftime('%H:%M') for t in trading_state['buy_times']]}")
        elif cmd == "exit":
            print("프로그램 종료")
            break
        else:
            print("잘못된 명령어")


def run_scheduled_mode():
    """자동 스케줄 모드 (실제 운영)"""
    schedule_trades()
    
    # APScheduler 설치 필요
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log("! APScheduler 설치 필요: pip install apscheduler")
        log("! 수동 모드로 전환합니다")
        run_manual_mode()
        return
    
    scheduler = BackgroundScheduler(timezone=str(KST))
    
    scheduler.add_job(
        lambda: buy_one() if should_buy() else None,
        'cron',
        day_of_week='mon-fri',
        minute='0,4,8,12,16,20,24,28,32,36,40,44,48,52,56',
        second='0',
        name='buy_cycle'
    )

    scheduler.add_job(
        lambda: sell_one() if should_sell() else None,
        'cron',
        day_of_week='mon-fri',
        minute='2,6,10,14,18,22,26,30,34,38,42,46,50,54,58',
        second='0',
        name='sell_cycle'
    )
    
    scheduler.start()
    log("스케줄러 시작됨")
    log("Ctrl+C 입력으로 종료\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("\n스케줄러 종료 중...")
        scheduler.shutdown()
        log("종료되었습니다")


def main():
    parser = argparse.ArgumentParser(description="주간 자동 매매")
    parser.add_argument(
        "--env",
        type=str,
        default="vps",
        choices=["prod", "vps"],
        help="실행 환경 (prod=실전, vps=모의)"
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="manual",
        choices=["manual", "schedule"],
        help="실행 모드 (manual=수동, schedule=자동)"
    )
    
    args = parser.parse_args()
    
    # 인증
    trading_state["is_paper"] = (args.env == "vps")

    log("KIS API 인증 중...")
    try:
        ka.auth(svr=args.env)
        log("✓ 인증 완료")
    except Exception as e:
        log(f"✗ 인증 실패: {e}")
        return
    
    # 현재 보유 확인
    get_positions()
    
    # 모드 선택
    if args.mode == "schedule":
        run_scheduled_mode()
    else:
        run_manual_mode()


if __name__ == "__main__":
    main()
