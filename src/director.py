import os
import json
import google.generativeai as genai
from src.drive_manager import fetch_latest_episode_state, create_episode_document

def generate_connected_script(market_data):
    print("[Director] Gemini API 기반 서사 체이닝 및 대본 작성 시작...")
    prev_state = fetch_latest_episode_state()
    next_ep = prev_state.get("episode", 101) + 1
    
    tnx = market_data.get("TNX", {}).get("close", 0)
    vix = market_data.get("VIX", {}).get("close", 0)
    
    if tnx > 4.5:
        villain, theme = "Debt Titan", "긴축의 심화와 방어선 사수"
    elif vix > 25.0:
        villain, theme = "Chaos Reaper", "변동성 폭발과 시장의 광기"
    else:
        villain, theme = "Bull Brute", "유동성 장세와 돌파 매수"
        
    script_data = {
        "episode": next_ep,
        "villain": villain,
        "theme": theme,
        "narration": f"오늘 시장 지표 분석 결과, {villain}의 기운이 감지되었다."
    }
    
    print(f"[Director] 매칭 완료 - 빌런: {villain}, 테마: {theme}")
    create_episode_document(script_data)
    return script_data
