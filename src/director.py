from src.drive_manager import fetch_latest_episode_state, create_episode_document, sync_process_design_document

def generate_connected_script(market_data):
    sync_process_design_document(version="v2.0_Final")
    prev_state = fetch_latest_episode_state()
    next_ep_num = prev_state["episode"] + 1
    villain = "Debt Titan" if market_data["TNX"]["close"] > 4.5 else "Chaos Reaper"
    print(f"[Director] Chaining narrative: Ep.{prev_state['episode']} ➔ Ep.{next_ep_num} ({villain})")
    script = {"episode": next_ep_num, "villain": villain, "theme": "긴축의 심화와 방어선 사수"}
    create_episode_document(script)
    return script
