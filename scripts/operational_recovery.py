"""Explicit operator recovery. No generation; new sessions require recorded absence confirmation."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
from src.pipeline_control import Control


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['inspect','attach-video','confirm-absent','grant-extra','resume','resume-existing','invalidate-asset'])
    p.add_argument('--day',required=True,help='KST YYYY-MM-DD')
    p.add_argument('--hero',required=True,choices=['EDT','GOC'])
    p.add_argument('--group',default='production',choices=['production','preview','full-preview'])
    p.add_argument('--reason');p.add_argument('--video-id')
    p.add_argument('--verified-absent',action='store_true')
    p.add_argument('--stage',choices=['text','image','tts','render','text-response']);p.add_argument('--asset')
    p.add_argument('--extra',type=int,choices=[1,2])
    a=p.parse_args();c=Control(a.group,a.hero);c.day=a.day
    # Changing the requested day also changes durable object paths.
    from datetime import date
    date.fromisoformat(a.day)
    c.slot=f'{c.channel}/{c.day}/{c.group}'
    if a.action=='inspect':
        print(json.dumps(c.status(),ensure_ascii=False))
        return
    # Never create a historical or missing slot merely to inspect/resume it.
    if a.action in ('resume','resume-existing') and not c.status().get('exists'):
        raise RuntimeError('existing_slot_required')
    c.claim()
    try:
        if a.action=='attach-video':
            if not a.reason or not a.video_id:p.error('--reason and --video-id required')
            from src.publisher import get_youtube_service
            service=get_youtube_service()
            if service is None:raise RuntimeError('youtube_credentials_missing')
            rows=service.videos().list(part='snippet,status',id=a.video_id).execute().get('items',[])
            if len(rows)!=1 or rows[0]['status']['privacyStatus']!='private':
                raise RuntimeError('private_video_not_verified')
            receipt=c.upload();metadata=receipt.get('metadata') or {}
            from src.publisher import build_title
            if rows[0]['snippet']['title']!=build_title(metadata):
                raise RuntimeError('video_title_mismatch_review_required')
            c.rpc('reconcile',{'video_id':a.video_id,'reason':a.reason})
            from src.drive_manager import update_episode
            update_episode(receipt['episode_id'], status=receipt.get('episode_status','published'), youtube_video_id=a.video_id)
            print('receipt linked and episode synchronized')
        elif a.action=='invalidate-asset':
            if not a.reason or not a.stage or a.asset is None:p.error('--reason --stage --asset required')
            c.rpc('invalidate_asset',{'key':f'{a.stage}:{a.asset}','reason':a.reason})
            print('cached asset invalidated; daily attempt counts retained')
        elif a.action in ('resume','resume-existing'):
            if a.group!='production':p.error('resume requires production group')
            receipt=c.upload()
            if not receipt:
                raise RuntimeError('rendered_upload_receipt_required; inspect text checkpoints before run-today')
            from src.drive_manager import update_episode
            if receipt.get('state')=='uploaded':
                video_id=receipt['video_id']
            else:
                from src.pipeline_control import activate,reset
                from src.asset_store import restore,verify_store
                from src.publisher import upload_to_youtube
                verify_store();token=activate(c)
                try:
                    if receipt.get('state') in ('unknown','session_creating'):
                        raise RuntimeError('reconciliation_required_before_resume')
                    if not restore('video','final',receipt['metadata'],'output_short.mp4'):
                        raise RuntimeError('original_asset_unavailable')
                    video_id=upload_to_youtube('output_short.mp4',receipt['metadata'])
                    if not video_id:raise RuntimeError('youtube_recovery_no_video_id')
                finally:reset(token)
            update_episode(receipt['episode_id'],status=receipt.get('episode_status','published'),youtube_video_id=video_id)
            print('existing slot recovered without regeneration')
        elif a.action=='confirm-absent':
            if not a.reason or not a.verified_absent:p.error('--reason and --verified-absent required after channel review')
            c.rpc('reconcile',{'verified_absent':True,'reason':a.reason})
            print('one manual restart authorized for original asset')
        else:
            if not all((a.reason,a.stage,a.asset is not None,a.extra)):p.error('--reason --stage --asset --extra required')
            if a.stage=='text-response':p.error('text-response is only for invalidate-asset; use text/storyboard for grant-extra')
            c.rpc('override',{'reason':a.reason,'stage':a.stage,'asset':a.asset,'extra':a.extra})
            print('finite extra attempts recorded; expires with this KST slot')
    finally:c.release()

if __name__=='__main__':main()
