import logging


logger = logging.getLogger(__name__)

def fetch_latest_episode_state():
    logger.info("episode_state_fetch_started backend=stub")
    # 프로덕션에서는 Google Drive API를 통해 최신 문서를 조회합니다.
    # 안전한 파일럿 가동을 위해 Fallback 로직 적용
    return {"episode": 101, "villain": "Debt Titan", "combat_state": "Defensive Standoff"}

def create_episode_document(script_data):
    logger.info("episode_archive_started episode=%s backend=stub", script_data.get("episode"))
    # 프로덕션에서는 Google Docs API를 통해 문서를 생성하고 Series_Bible 폴더에 넣습니다.
    logger.info(
        "episode_archive_finished document=Ep.%s_%s_Script.gdoc",
        script_data.get("episode"),
        script_data.get("villain"),
    )
