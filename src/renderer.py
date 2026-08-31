import os
import subprocess

def render_video(script_data):
    output_path = "output_short.mp4"
    if os.path.exists(output_path):
        os.remove(output_path)

    print("[Renderer] FFmpeg을 이용해 9:16 모션 코믹스 영상 렌더링 시작...")
    
    ep_num = script_data.get('episode', 101)
    villain = script_data.get('villain', 'Unknown')
    text_content = f"EDT Universe Ep.{ep_num}\nVs. {villain}"
    
    # 8초 분량의 1080x1920 영상 생성 (실제 프로덕션 FFmpeg 명령어)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=8",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", f"drawtext=text='{text_content}':fontcolor=orange:fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2",
        "-c:v", "libx264", "-t", "8", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        output_path
    ]
    
    subprocess.run(cmd, check=True)
    print(f"[Renderer] 최종 비디오 생성 완료: {output_path}")
    return output_path
