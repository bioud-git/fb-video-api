from flask import Flask, request, jsonify
import yt_dlp
import requests
import os
import tempfile
import urllib.parse

app = Flask(__name__)

IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')

@app.route('/')
def home():
    return "الخادم يعمل بنجاح ويدعم مقاطع الفيديو والصور!"

def _collect_image_candidates(info):
    """Return a list of candidate URLs from yt-dlp info that may point to images."""
    candidates = []
    if info.get('url'):
        candidates.append(info.get('url'))
    if info.get('thumbnail'):
        candidates.append(info.get('thumbnail'))
    # thumbnails may be a list of dicts
    thumbnails = info.get('thumbnails') or info.get('thumbnails')
    if isinstance(thumbnails, list):
        for t in thumbnails:
            if isinstance(t, dict) and t.get('url'):
                candidates.append(t.get('url'))
    # dedupe while preserving order
    seen = set()
    out = []
    for u in candidates:
        if not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

def _infer_ext_from_url(url):
    try:
        p = urllib.parse.urlparse(url)
        root, ext = os.path.splitext(p.path)
        return ext.lstrip('.').lower()
    except Exception:
        return ''

@app.route('/api/download', methods=['GET'])
def download_video():
    url = request.args.get('url')
    if not url:
        return jsonify({"status": "error", "message": "الرجاء إرسال الرابط"})

    cookies = request.headers.get('Cookie')

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    if cookies:
        ydl_opts['http_headers'] = {'Cookie': cookies}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # If it's a playlist/carousel, consider only the first entry for static posts
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            formats_list = []
            seen_resolutions = set()

            if 'formats' in info:
                for f in info['formats']:
                    ext = f.get('ext', '')
                    vcodec = f.get('vcodec', 'none')
                    height = f.get('height')

                    # --- VIDEO logic (unchanged) ---
                    is_video = ext in ['mp4', 'webm'] and vcodec != 'none' and height
                    # --- end video logic ---

                    is_image = ext in ['jpg', 'jpeg', 'png', 'webp']

                    if is_video or is_image:
                        res_str = f"{height}p" if height else "صورة"
                        unique_key = f"{res_str}_{ext}"
                        if unique_key not in seen_resolutions:
                            seen_resolutions.add(unique_key)
                            formats_list.append({
                                "resolution": res_str,
                                "url": f.get('url'),
                                "height": height or 0,
                                "ext": ext
                            })

                formats_list = sorted(formats_list, key=lambda k: k['height'], reverse=True)

            # FALLBACK: handle direct image URLs returned by yt-dlp for Instagram static posts (/p/)
            if not formats_list:
                candidates = _collect_image_candidates(info)
                for u in candidates:
                    ext = (info.get('ext') or '').lower() or _infer_ext_from_url(u)
                    if ext in IMAGE_EXTS:
                        formats_list.append({
                            "resolution": "صورة",
                            "url": u,
                            "height": 0,
                            "ext": ext
                        })
                        break

            # Final fallback: keep previous behavior (don't change video logic)
            if not formats_list:
                ext = info.get('ext', 'mp4')
                formats_list.append({
                    "resolution": "جودة أصلية",
                    "url": info.get('url', ''),
                    "height": 0,
                    "ext": ext
                })

            return jsonify({
                "status": "success",
                "title": info.get('title', 'Media'),
                "formats": formats_list
            })
    except Exception as e:
        return jsonify({"status": "error", "message": f"خطأ: {str(e)}"})

@app.route('/api/telegram', methods=['POST'])
def send_to_telegram():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "بيانات مفقودة"})

    video_url = data.get('url')
    bot_token = data.get('bot_token')
    chat_id = data.get('chat_id')
    cookies = data.get('cookies')
    original_url = data.get('original_url', '')

    if not video_url or not bot_token or not chat_id:
        return jsonify({"status": "error", "message": "بيانات ناقصة"})

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        outtmpl = os.path.join(tmp_dir, '%(id)s.%(ext)s')

        # Preflight: extract info to detect direct image posts without altering video flow
        pre_opts = {
            'quiet': True,
            'no_warnings': True,
        }
        if cookies:
            pre_opts['http_headers'] = {'Cookie': cookies}

        image_direct_url = None
        image_ext = None

        with yt_dlp.YoutubeDL(pre_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            # If formats exist and indicate video formats, we will let the normal download proceed (video path unchanged)
            has_video_format = False
            if 'formats' in info:
                for f in info['formats']:
                    extf = f.get('ext', '')
                    vcodec = f.get('vcodec', 'none')
                    height = f.get('height')
                    if extf in ['mp4', 'webm'] and vcodec != 'none' and height:
                        has_video_format = True
                        break

            # If no video formats, look for direct image URL candidates
            if not has_video_format:
                candidates = _collect_image_candidates(info)
                for u in candidates:
                    ext_candidate = (info.get('ext') or '').lower() or _infer_ext_from_url(u)
                    if ext_candidate in IMAGE_EXTS:
                        image_direct_url = u
                        image_ext = ext_candidate
                        break

        # If we found a direct image URL, download it with requests (keep video flow untouched)
        if image_direct_url:
            # download image to tmp_dir with proper extension
            filename = f"downloaded_image.{image_ext}"
            file_path = os.path.join(tmp_dir, filename)
            headers = {}
            if cookies:
                headers['Cookie'] = cookies
            resp = requests.get(image_direct_url, headers=headers, stream=True, timeout=30)
            if resp.status_code != 200:
                return jsonify({"status": "error", "message": "فشل تحميل الصورة من المصدر"})
            with open(file_path, 'wb') as fh:
                for chunk in resp.iter_content(1024 * 8):
                    if chunk:
                        fh.write(chunk)
            ext = os.path.splitext(file_path)[1].lower()
        else:
            # No direct image detected: proceed with existing yt-dlp download logic (unchanged for videos)
            ydl_opts = {
                'format': 'best',
                'outtmpl': outtmpl,
                'quiet': True,
                'no_warnings': True,
            }
            if cookies:
                ydl_opts['http_headers'] = {'Cookie': cookies}

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            downloaded_files = os.listdir(tmp_dir)
            if not downloaded_files:
                return jsonify({"status": "error", "message": "فشل التحميل"})

            file_path = os.path.join(tmp_dir, downloaded_files[0])
            ext = os.path.splitext(file_path)[1].lower()

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > 50:
            return jsonify({"status": "error", "message": "الملف كبير جداً"})

        tg_data = {'chat_id': chat_id}
        if original_url:
            tg_data['caption'] = original_url

        # Use sendPhoto for images and sendVideo for videos (behavior preserved for videos)
        if ext in ('.jpg', '.jpeg', '.png', '.webp'):
            telegram_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            file_key = 'photo'
        else:
            telegram_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
            file_key = 'video'
            tg_data['supports_streaming'] = 'true'
        
        with open(file_path, 'rb') as media_file:
            response = requests.post(telegram_url, data=tg_data, files={file_key: media_file}, timeout=120)

        if response.status_code == 200 and response.json().get('ok'):
            return jsonify({"status": "success", "message": "تم الإرسال بنجاح"})
        else:
            return jsonify({"status": "error", "message": "فشل الإرسال إلى تيليغرام"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            for f in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, f))
                except Exception:
                    pass
            try:
                os.rmdir(tmp_dir)
            except Exception:
                pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
