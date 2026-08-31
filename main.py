from src.collector import fetch_market_data
from src.director import generate_connected_script
from src.renderer import render_video
from src.publisher import upload_to_youtube

def main():
    print("=== EDT Universe Market Shorts: PRODUCTION PIPELINE STARTED ===")
    market_data = fetch_market_data()
    script = generate_connected_script(market_data)
    video_file = render_video(script)
    upload_to_youtube(video_file, script)
    print("=== PRODUCTION PIPELINE FINISHED ===")

if __name__ == "__main__":
    main()
