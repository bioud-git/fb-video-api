from flask import Flask, request, jsonify
import yt_dlp
import requests
import os
import tempfile

app = Flask(__name__)

@app.route('/')
def home():
    return "الخادم يعمل بنجاح ويدعم الجودات المتعددة!"

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
            formats_list = []
            seen_resolutions = set()

            if 'formats' in info:
                for f in info['formats']:
                    ext = f.get('ext', '')
                    vcodec = f.get('vcodec', 'none')
                    height = f.get('height')

                    if ext == 'mp4' and vcodec != 'none' and height:
                        res_str = f"{height}p"
                        if res_str not in seen_resolutions:
                            seen_resolutions.add(res_str)
                            formats_list.append({
                                "resolution": res_str,
                                "url": f.get('url'),
                                "height": height
                            })

                formats_list = sorted(formats_list, key=lambda k: k['height'], reverse=True)

            if not formats_list:
                formats_list.append({
                    "resolution": "جودة افتراضية",
                    "url": info.get('url', ''),
                    "height": 0
                })

            return jsonify({
                "status": "success",
                "title": info.get('title', 'Video'),
                "formats": formats_list
            })
    except yt_dlp.utils.DownloadError as e:
        return jsonify({"status": "error", "message": f"فشل الاستخراج: {str(e)}"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"خطأ غير متوقع: {str(e)}"})


@app.route('/api/telegram', methods=['POST'])
def send_to_telegram():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "الرجاء إرسال البيانات بصيغة JSON"})

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

        ydl_opts = {
            'format': 'best[ext=mp4]/best',
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
            return jsonify({"status": "error", "message": "فشل تحميل الفيديو"})

        video_path = os.path.join(tmp_dir, downloaded_files[0])
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)

        if file_size_mb > 50:
            return jsonify({"status": "error", "message": f"حجم الملف يتجاوز الحد المسموح (50 MB)"})

        telegram_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
        tg_data = {
            'chat_id': chat_id, 
            'supports_streaming': 'true'
        }
        
        if original_url:
            tg_data['caption'] = original_url
        
        with open(video_path, 'rb') as video_file:
            response = requests.post(telegram_url, data=tg_data, files={'video': video_file}, timeout=120)

        if response.status_code == 200 and response.json().get('ok'):
            return jsonify({"status": "success", "message": "تم الإرسال بنجاح"})
        else:
            return jsonify({"status": "error", "message": "فشل الإرسال إلى تيليغرام"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            for f in os.listdir(tmp_dir):
                os.remove(os.path.join(tmp_dir, f))
            os.rmdir(tmp_dir)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
