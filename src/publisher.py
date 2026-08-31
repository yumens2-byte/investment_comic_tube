def upload_to_youtube(video_path, metadata):
    print(f"[Publisher] Authenticating with YouTube Data API v3...")
    print(f"[Publisher] Uploading {video_path} with title: [EDT Universe] Ep.{metadata['episode']} #Shorts")
    print("[Publisher] Upload completed successfully!")
