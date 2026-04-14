import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

# -*- coding: utf-8 -*-
"""
황작가 AI 스튜디오 v1.0 — 완전 통합판
Flask 로컬 서버 + 브라우저 UI

탭 구성:
  1. 🎙️ TTS        — ElevenLabs 음성 생성
  2. 🎨 이미지 생성  — Gemini 화풍 12종 + 구분자별 배치 생성
  3. 📊 Gamma PPT   — Gamma API PPT/PDF 생성
  4. 🖼️ 썸네일      — 나노바나나 스타일 썸네일 생성
  5. 🎬 영상        — Grok API 이미지→영상 변환
  6. ⚙️ 설정        — 모든 API 키 통합관리

pip install flask requests google-generativeai pillow
python 황작가_스튜디오.py
"""

import os, re, io, time, json, threading, datetime, traceback, subprocess, sys, webbrowser, base64, queue
from typing import Optional, List, Dict, Any
from pathlib import Path

try:
    from flask import Flask, request, jsonify, Response
except ImportError:
    print("pip install flask requests 를 먼저 실행하세요")
    sys.exit(1)

import requests as req

# ── optional deps ──────────────────────────────────────
try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GENAI = True
except Exception:
    # 신버전 없으면 구버전 fallback
    try:
        import google.generativeai as _genai_old
        # 구버전을 신버전처럼 쓸 수 있도록 래퍼
        class _GenaiCompat:
            def __init__(self): pass
            def Client(self, api_key=None):
                _genai_old.configure(api_key=api_key)
                return _GenaiClientCompat()
        class _GenaiClientCompat:
            class models:
                @staticmethod
                def generate_content(model, contents, config=None):
                    m = _genai_old.GenerativeModel(
                        model,
                        generation_config=_genai_old.GenerationConfig(response_modalities=["IMAGE"])
                    )
                    return m.generate_content(contents)
        class _GenaiTypes:
            class GenerateContentConfig:
                def __init__(self, **kwargs): pass
        genai = _GenaiCompat()
        genai_types = _GenaiTypes()
        HAS_GENAI = True
    except Exception:
        genai = None; genai_types = None; HAS_GENAI = False

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    Image = None; HAS_PIL = False

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except Exception:
    AudioSegment = None; HAS_PYDUB = False

try:
    from pptx import Presentation
    from pptx.util import Inches
    HAS_PPTX = True
except Exception:
    HAS_PPTX = False

# ── 상수 ──────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PORT         = 7861
# OneDrive/네트워크 드라이브에서 실행 시 권한 문제 방지
# 스크립트 폴더에 쓰기 가능하면 그 안에, 안 되면 사용자 홈 폴더에 생성
def _get_default_out():
    candidate = os.path.join(SCRIPT_DIR, "studio_output")
    try:
        os.makedirs(candidate, exist_ok=True)
        # 실제 쓰기 테스트
        test = os.path.join(candidate, ".write_test")
        open(test, 'w').close()
        os.remove(test)
        return candidate
    except Exception:
        # 홈 폴더로 fallback
        fallback = os.path.join(os.path.expanduser("~"), "황작가_studio_output")
        os.makedirs(fallback, exist_ok=True)
        return fallback

DEFAULT_OUT = _get_default_out()
GAMMA_BASE   = "https://public-api.gamma.app/v1.0"
EL_BASE      = "https://api.elevenlabs.io/v1"
GEMINI_IMG_MODEL = "models/gemini-3.1-flash-image-preview"

# ── API 키 파일 경로 ────────────────────────────────────
KEY_FILES = {
    "elevenlabs": os.path.join(SCRIPT_DIR, "elevenlabs_api_key.txt"),
    "gemini":     os.path.join(SCRIPT_DIR, "api_key.txt"),
}

# ── 화풍 12종 ──────────────────────────────────────────
# UI 표시용 화풍 목록 (실제 생성은 STYLE_FUNC_MAP의 원본 함수 사용)
COMIC_STYLES: Dict[str, str] = {
    "📈 크립토툰":    "인디 카툰 / 주식·코인 만화",
    "📰 한국어 뉴스": "한국어 뉴스 캡처 스타일",
    "🎭 팝아트":      "팝아트 스타일",
    "🎮 마인크래프트":"3D 복셀 / 마인크래프트",
    "🦠 미니 세포":   "미니 세포 캐릭터 (수채화 웹툰)",
    "💥 시바 짤방":   "그림판 짤방 (MS Paint 밈)",
    "🎨 수채화 명화": "수채화 명화 스타일",
    "🧸 코인가이드":  "픽사 3D CGI (코인가이드)",
    "🖼️ 칠판 강의":   "칠판 강의 스타일",
    "🍫 찰흙 클레이": "찰흙 클레이 스타일",
    "📱 숏츠 세로":   "숏츠 세로 (9:16)",
    "📸 인스타 감성":  "인스타 감성 일러스트",
    "📊 인포그래픽":  "인포그래픽 / 카드뉴스",
    "🎞️ 레트로":      "레트로 / 빈티지 스타일",
    "📖 웹툰":         "한국 웹툰 스타일",
}

VIP_KEYWORDS = [
    "트럼프","머스크","일론","이재용","파월","겐슬러","바이든","해리스",
    "창펑자오","비탈릭","부테린","세일러","워렌 버핏","푸틴","시진핑",
    "최태원","정의선","구광모","팀쿡","저커버그"
]

# 모델별 파라미터 지원 여부
EL_MODELS = [
    {
        "id":   "eleven_v3",
        "name": "Eleven v3 ✨",
        "desc": "최고 표현력 · 감정 풍부 · 74개 언어 · 최대 3,000자",
        "supports_style":         True,
        "supports_speaker_boost": False,   # v3 미지원
        "supports_speed_slider":  False,   # v3는 audio tag로 제어
        "supports_enhance":       False,  # previous_text/next_text 미지원
        "max_chars": 3000,
    },
    {
        "id":   "eleven_multilingual_v2",
        "name": "Multilingual v2",
        "desc": "고품질 자연스러운 감정 표현 · 29개 언어 · 최대 10,000자",
        "supports_style":         True,
        "supports_speaker_boost": True,
        "supports_speed_slider":  True,
        "supports_enhance":       True,
        "max_chars": 10000,
    },
    {
        "id":   "eleven_flash_v2_5",
        "name": "Flash v2.5 ⚡",
        "desc": "초저지연 75ms · 32개 언어 · 최대 40,000자 · 크레딧 0.5배",
        "supports_style":         True,
        "supports_speaker_boost": True,
        "supports_speed_slider":  True,
        "supports_enhance":       True,
        "max_chars": 40000,
    },
    {
        "id":   "eleven_turbo_v2_5",
        "name": "Turbo v2.5",
        "desc": "빠름 · 32개 언어 · 최대 40,000자 · 크레딧 0.5배",
        "supports_style":         True,
        "supports_speaker_boost": True,
        "supports_speed_slider":  True,
        "supports_enhance":       True,
        "max_chars": 40000,
    },
    {
        "id":   "eleven_flash_v2",
        "name": "Flash v2 (영어전용)",
        "desc": "초저지연 · 영어 전용 · 최대 40,000자 · 크레딧 0.5배",
        "supports_style":         True,
        "supports_speaker_boost": True,
        "supports_speed_slider":  True,
        "supports_enhance":       True,
        "max_chars": 40000,
    },
]
# id → 메타 빠른 조회
EL_MODEL_MAP = {m["id"]: m for m in EL_MODELS}
EL_FORMATS = ["mp3_44100_128","mp3_44100_192","mp3_22050_32","pcm_16000","pcm_22050","pcm_24000","pcm_44100"]

# ── 유틸 ──────────────────────────────────────────────
def ensure_dir(p):
    # 상대경로면 절대경로로 변환 (OneDrive 등 권한 문제 방지)
    p = os.path.abspath(p)
    try:
        os.makedirs(p, exist_ok=True)
        return p
    except PermissionError:
        # 쓰기 불가 경로면 홈 폴더 아래로 재매핑
        try:
            rel = os.path.relpath(p, SCRIPT_DIR)
        except ValueError:
            rel = os.path.basename(p)
        p2 = os.path.join(os.path.expanduser("~"), "황작가_studio", rel)
        os.makedirs(p2, exist_ok=True)
        print(f"[폴더 fallback] {p} → {p2}")
        return p2
    except Exception:
        # 기타 에러도 홈으로
        p2 = os.path.join(os.path.expanduser("~"), "황작가_studio", os.path.basename(p))
        os.makedirs(p2, exist_ok=True)
        return p2
def now_ts(): return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]

def read_key(service: str) -> Optional[str]:
    env_map = {"elevenlabs":"ELEVENLABS_API_KEY","gemini":"GEMINI_API_KEY","gamma":"GAMMA_API_KEY","xai":"XAI_API_KEY"}
    raw = os.getenv(env_map.get(service,""), "")
    path = KEY_FILES.get(service,"")
    if not raw and os.path.exists(path):
        try:
            with open(path,"r",encoding="utf-8-sig") as f: raw=f.read()
        except: return None
    raw = raw.strip().replace("\ufeff","")
    m = re.search(r"[A-Za-z0-9_\-]{20,}", raw)
    return m.group(0) if m else None

def save_key(service: str, key: str):
    path = KEY_FILES.get(service)
    if path:
        with open(path,"w",encoding="utf-8") as f: f.write(key.strip())

import re as _re
_SPLIT_RE = _re.compile(r"(?:^|\r?\n)\s*-{3,}[-<\s]*(?:\r?\n|$)")

def parse_scenes(script):
    raw = (script or "").strip()
    if not raw: return []
    return [c.strip() for c in _SPLIT_RE.split(raw) if c and c.strip()]

def ext_for_fmt(fmt): return "mp3" if fmt.startswith("mp3") else "wav"

def safe_req(method, url, **kw):
    kw.setdefault("timeout", 30)
    hdrs = kw.pop("headers",{}) or {}
    hdrs.setdefault("User-Agent","Mozilla/5.0")
    kw["headers"] = hdrs
    for _ in range(3):
        try:
            r = req.request(method, url, **kw)
            if r.status_code in (429,502,503,504): time.sleep(1.5); continue
            return r
        except Exception as e:
            time.sleep(1.5)
    raise Exception("Request failed")

# ── SSE 로그 ──────────────────────────────────────────
_log_qs: dict = {}   # tab_id → queue
_log_lock = threading.Lock()
_tab_cancel: dict = {}  # tab_id → Event
_cancel = threading.Event()

def bcast(msg: str, tab_id: str = None):
    with _log_lock:
        if tab_id:
            q = _log_qs.get(tab_id)
            if q:
                try: q.put_nowait(msg)
                except: pass
        else:
            dead = []
            for tid, q in list(_log_qs.items()):
                try: q.put_nowait(msg)
                except: dead.append(tid)
            for tid in dead: _log_qs.pop(tid, None)

# ── ElevenLabs API ─────────────────────────────────────
def el_hdrs(k): return {"xi-api-key":k,"Content-Type":"application/json","Accept":"application/json"}

def el_voices(k):
    r = req.get(f"{EL_BASE}/voices", headers=el_hdrs(k), timeout=15)
    r.raise_for_status(); return r.json().get("voices",[])

def el_models_api(k):
    r = req.get(f"{EL_BASE}/models", headers=el_hdrs(k), timeout=15)
    r.raise_for_status(); return r.json()

def el_user(k):
    r = req.get(f"{EL_BASE}/user", headers=el_hdrs(k), timeout=15)
    if r.status_code==401: raise Exception("401 Unauthorized")
    r.raise_for_status(); return r.json()

# v3 Audio Tags — ElevenLabs 공식 지원 태그 목록
V3_EMOTION_TAGS = [
    "happy","sad","excited","angry","fearful","disgusted","surprised",
    "thoughtful","confused","disappointed","determined","curious",
    "energetic","calm","nervous","embarrassed","proud","annoyed",
    "sarcastic","whispering","shouting","laughing","crying",
    "firm","gentle","serious","playful","mysterious",
    "questioning","acknowledging","explaining","emphasizing",
    "chuckling","sighing","gasping","clearing throat",
    "fast","slow","cheerful","melancholic","dramatic",
]

GEMINI_TEXT_MODEL = "models/gemini-2.0-flash"

def inject_v3_audio_tags(gemini_key: str, text: str, full_script: str = "") -> str:
    """Gemini Flash로 대본을 분석해 ElevenLabs v3 Audio Tags를 자동 삽입합니다."""
    if not HAS_GENAI or not gemini_key:
        return text
    try:
        tag_list = ", ".join(f"[{t}]" for t in V3_EMOTION_TAGS)
        prompt = f"""You are an expert voice director for ElevenLabs Eleven v3 TTS.
Insert emotion/audio tags to make the script sound expressive and natural.
RULES:
1. Insert tags like [happy], [excited], [thoughtful], [whisper] etc. at the START of sentences where tone changes.
2. Keep original Korean text 100% intact — only ADD tags.
3. Use only these tags: {tag_list}
4. Output ONLY the tagged version of the CURRENT TEXT — nothing else.
FULL SCRIPT CONTEXT: {full_script[:800] if full_script else text}
CURRENT TEXT TO TAG: {text}"""

        _c = genai.Client(api_key=re.sub(r"\s+", "", gemini_key.strip()))
        resp = _c.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        tagged = (resp.text or "").strip()
        orig_words = set(re.sub(r"\[.+?\]","",text).split())
        tagged_words = set(re.sub(r"\[.+?\]","",tagged).split())
        if orig_words and len(orig_words & tagged_words) / len(orig_words) < 0.85:
            return text
        return tagged
    except Exception:
        return text


def el_tts(k, voice_id, text, model_id, stability, similarity, style, spk_boost, speed, fmt,
           prev_text=None, next_text=None, enhance=False, gemini_key=None, full_script=None):
    """ElevenLabs TTS — 모델별 파라미터 자동 분기

    eleven_v3 + enhance=True:
      - Gemini Flash로 대본 분석 → 감정/톤 Audio Tags 자동 삽입
      - Speed: [slowly]/[quickly] 태그로 변환
      - Speaker Boost: 미지원
    나머지 모델 + enhance=True:
      - previous_text / next_text 컨텍스트 주입 (자연스러운 억양 연결)
    """
    meta   = EL_MODEL_MAP.get(model_id, {})
    is_v3  = (model_id == "eleven_v3")
    spd    = round(float(speed), 2)

    # v3 + enhance: Gemini로 Audio Tags 삽입
    actual_text = text
    if is_v3 and enhance and gemini_key:
        actual_text = inject_v3_audio_tags(gemini_key, text, full_script or text)

    # v3: Speed → audio tag (enhance로 이미 태그 삽입됐으면 앞에 추가)
    if is_v3:
        if spd <= 0.82:   actual_text = f"[slowly] {actual_text}"
        elif spd >= 1.18: actual_text = f"[quickly] {actual_text}"

    # ── 끝 잘림 방지 ────────────────────────────────────
    # ElevenLabs가 문장 끝을 열어두다가 갑자기 끊기는 현상 방지
    # 1) 텍스트 끝에 마침표가 없으면 추가 (완전히 끝났다는 신호)
    # 2) 뒤에 빈 next_text 공백을 주면 억양이 자연스럽게 내려앉음
    actual_text = actual_text.rstrip()
    if actual_text and actual_text[-1] not in '.!?。':
        actual_text = actual_text + '.'

    # voice_settings 구성
    voice_settings = {
        "stability":        round(float(stability), 3),
        "similarity_boost": round(float(similarity), 3),
        "style":            round(float(style), 3),
    }
    if meta.get("supports_speaker_boost", True):
        voice_settings["use_speaker_boost"] = bool(spk_boost)

    payload = {
        "text":           actual_text,
        "model_id":       model_id,
        "voice_settings": voice_settings,
    }

    # Speed: v3 제외, 최상위 필드
    if not is_v3:
        payload["speed"] = round(spd, 3)

    # Enhance: 앞뒤 문맥 주입 (v3 미지원 — API 에러 방지)
    if enhance and not is_v3:
        if prev_text: payload["previous_text"] = str(prev_text)
        if next_text: payload["next_text"]     = str(next_text)
        payload["apply_text_normalization"] = "auto"
    elif not is_v3:
        # Enhance 꺼도 next_text에 빈 문자열 → 억양이 자연스럽게 닫힘
        if not next_text:
            payload["next_text"] = " "

    hdrs = {"xi-api-key":k,"Content-Type":"application/json","Accept":"audio/mpeg"}
    r = req.post(
        f"{EL_BASE}/text-to-speech/{voice_id}",
        headers=hdrs,
        params={"output_format": fmt},
        json=payload,
        timeout=120,
        stream=True,
    )
    r.raise_for_status()
    audio = b"".join(r.iter_content(8192))

    # 무음 패딩 추가 (끝 잘림 방지 — 0.4초)
    if HAS_PYDUB and audio:
        try:
            import io as _io
            is_mp3 = fmt.startswith("mp3")
            seg = AudioSegment.from_mp3(_io.BytesIO(audio)) if is_mp3 else AudioSegment.from_raw(_io.BytesIO(audio), sample_width=2, frame_rate=24000, channels=1)
            silence = AudioSegment.silent(duration=400)  # 400ms
            padded = seg + silence
            out = _io.BytesIO()
            padded.export(out, format="mp3" if is_mp3 else "wav")
            audio = out.getvalue()
        except Exception:
            pass  # pydub 실패 시 원본 반환

    return audio



def el_history(k, n=20):
    r = req.get(f"{EL_BASE}/history",headers=el_hdrs(k),params={"page_size":n},timeout=15)
    r.raise_for_status(); return r.json().get("history",[])

def el_clone(k, name, file_paths, desc=""):
    hdrs = {"xi-api-key":k,"Accept":"application/json"}
    files = [("name",(None,name)),("description",(None,desc))]
    handles = []
    for fp in file_paths:
        fh=open(fp,"rb"); handles.append(fh)
        files.append(("files",(os.path.basename(fp),fh,"audio/mpeg")))
    try:
        r = req.post(f"{EL_BASE}/voices/add",headers=hdrs,files=files,timeout=120)
        r.raise_for_status(); return r.json()
    finally:
        for fh in handles: fh.close()

# ── Gamma API ─────────────────────────────────────────
def gamma_post(k, path, payload):
    r = safe_req("POST",f"{GAMMA_BASE}{path}",headers={"X-API-KEY":k,"Content-Type":"application/json"},json=payload)
    if r.status_code>=400: raise Exception(r.text)
    try: return r.json()
    except: return {"raw":r.text}

def gamma_get(k, path, params=None):
    r = safe_req("GET",f"{GAMMA_BASE}{path}",headers={"X-API-KEY":k},params=params or {})
    try: return r.json()
    except: return {"raw":r.text,"statusCode":r.status_code}

def gamma_themes(k):
    items=[]; after=None
    for _ in range(10):
        params={"limit":50}
        if after: params["after"]=after
        resp=gamma_get(k,"/themes",params)
        for t in resp.get("data") or []:
            tid=(t.get("id") or "").strip()
            nm=(t.get("name") or "").strip() or "(이름없음)"
            if tid: items.append({"id":tid,"name":nm,"type":t.get("type","")})
        after=resp.get("nextCursor")
        if not after: break
    return items

def find_url(obj, ext):
    ext=ext.lower().strip(".")
    if isinstance(obj,dict):
        for k2,v in obj.items():
            if k2 in ("pptxUrl","pdfUrl","exportUrl","downloadUrl","fileUrl","url") and isinstance(v,str) and v.lower().endswith("."+ext): return v
        for v in obj.values():
            found=find_url(v,ext)
            if found: return found
    elif isinstance(obj,list):
        for v in obj:
            found=find_url(v,ext)
            if found: return found
    return None

def gamma_generate_one(k, input_text, export_as, num_cards, image_source, image_style, theme_id, instructions, folder_id):
    payload={"inputText":input_text[:35000],"textMode":"condense","format":"presentation","exportAs":export_as,"numCards":int(num_cards),"imageOptions":{"source":image_source,"style":image_style},"textOptions":{"language":"ko"},"additionalInstructions":(instructions or "")[:2500]}
    if folder_id: payload["folderIds"]=[folder_id]
    if theme_id: payload["themeId"]=theme_id
    resp=gamma_post(k,"/generations",payload)
    gid=resp.get("generationId")
    if not gid: raise Exception(f"generationId 없음: {resp}")
    for _ in range(110):
        if _cancel.is_set(): return None
        time.sleep(2.5)
        g=gamma_get(k,f"/generations/{gid}")
        st=(g.get("status") or "").lower()
        if st=="completed":
            url=find_url(g,export_as)
            if not url: return None
            return url, g
        if st in ("failed","error"): return None
    return None

# ── 원본 이미지 생성 함수 (각 파일 원본 그대로) ────────────────
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

# google-generativeai (구버전) 방식 — 원본 코드 그대로
# (google.generativeai import는 상단에서 처리)

# VIP 키워드 (뉴스.py 기준)
VIP_KEYWORDS = [
    "트럼프", "머스크", "일론", "이재용", "파월", "겐슬러", "바이든", "해리스", 
    "창펑자오", "비탈릭", "부테린", "세일러", "워렌 버핏", "푸틴", "시진핑", 
    "최태원", "정의선", "구광모", "팀쿡", "저커버그"
]

# ==================================================
# 크립토툰
# ==================================================
_STYLE_gen_cryptotoon = (
    "Art Style: **Indie Comic / Modern Western Cartoon Style** (Generic, Hand-drawn feel). "
    "Key Visuals: **Thin, wobbly hand-drawn lines**, flat pastel and vibrant colors, very simple shading. "
    "Characters: **Create ORIGINAL characters**. Use simple geometric shapes (circle heads, bean-shaped bodies). "
    "Features: Dot eyes or simple expressive eyes, **noodle-like rubbery limbs** (rubbery hose animation style), exaggerated expressions. "
    "Atmosphere: Humorous, whimsical, quirky, and lighthearted 2D vector art. "
    "IMPORTANT: Do NOT copy specific characters from TV shows like Adventure Time. Create unique designs."
)

def gen_cryptotoon(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 설치되지 않았습니다. pip install google-generativeai")
    if not Image:
        raise Exception("Pillow가 설치되지 않았습니다. pip install pillow")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    # API 키 정제 (키 파일에 한글/설명 문구 섞이면 gRPC 'Illegal header value'가 터질 수 있음)
    api_key = (api_key or "").strip()
    api_key = re.sub(r"\s+", "", api_key)
    if not api_key:
        raise Exception("Gemini API Key가 비었습니다. api_key.txt 또는 환경변수 GEMINI_API_KEY를 확인하세요.")
    if any((ord(c) < 32) or (ord(c) > 126) for c in api_key):
        raise Exception("Gemini API Key에 비 ASCII 문자가 섞여있습니다. api_key.txt에는 키만 한 줄로 넣어주세요.")
    _client = genai.Client(api_key=api_key)
    _client = genai.Client(api_key=api_key)

    saved = []
    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 코믹스 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 생성 중...")
        try:
            prompt_parts = []
            style_instruction = ""

            if reference_image_path and os.path.exists(reference_image_path):
                ref_img = Image.open(reference_image_path)
                prompt_parts.append(ref_img)
                style_instruction = (
                    "Please MIMIC THE ARTISTIC STYLE (Line weight, Color palette, Character design) "
                    "of the provided reference image. Ensure consistency."
                )
            else:
                style_instruction = f"STYLE: {_STYLE_gen_cryptotoon}"

            final_prompt = f"""
Create a single, high-quality 16:9 cartoon panel.

[Content & Text Rules]
- Read the SCENE DESCRIPTION.
- Identify ONLY the most IMPORTANT short key phrase(s) (few words) and draw only those into the image.
- Do not draw long paragraphs.
- If Korean text is used, draw it in a natural hand-drawn comic font.

SCENE DESCRIPTION: {scene_text}

[Style & Design Rules]
{style_instruction}
- Flat, funny, hand-drawn cartoon panel.
- Clear visual storytelling.
- Simple cute characters with exaggerated expressions.
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            final_img = Image.open(io.BytesIO(img_data))
            filename = f"comic_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"✅ 저장: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3)

    return saved


# ==================================================
# 뉴스
# ==================================================
_CSTYLES_gen_news = {
    "📰 한국어 인터넷 뉴스 캡처 (Korean Full-Screen News)": (
        "Art Style: **A flat, full-screen digital screenshot of a modern KOREAN financial news website. ABSOLUTELY NO computer monitors, no bezels, no physical devices around it. The webpage interface MUST fill the entire canvas from edge to edge.** "
        "Key Visuals: **The layout MUST include a bold, large, dramatic news HEADLINE in KOREAN (Hangul) summarizing the scene. Below the headline, include a realistic 'Press Photo'. Include standard Korean webpage UI elements (like '뉴스', '증권', reporter name, timestamp) and columns of body text simulating a real Korean article layout.** "
        "Atmosphere: Authoritative, breaking news, journalistic, and highly realistic."
    ),
    "느와르 감성 2D 일러스트 (Noir 2D Webtoon)": (
        "Art Style: **High-quality 2D Korean webtoon / graphic novel illustration. Detailed hand-drawn colored pencil and watercolor style. ABSOLUTELY NO 3D rendering, NO hyper-realism.** "
        "Key Visuals: **Cinematic NOIR atmosphere, dramatic heavy shadows, low-key lighting, and a moody, serious color palette, but maintaining the charming 2D drawn aesthetic.** "
        "Background: **Highly detailed 2D illustrated environment.** "
        "Atmosphere: Dark, serious, high-stakes, dramatic, but artistically beautiful."
    )
}

def gen_news(api_key: str, full_script: str, scenes: List[str], out_dir: str, style_prompt: str, selected_style_name: str, reference_image_path: Optional[str] = None, cancel_event: Optional[threading.Event] = None, log_fn=None) -> List[str]:
    if not HAS_GENAI: raise Exception("google-generativeai 라이브러리가 필요합니다.")
    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn: log_fn(msg)

    _client = genai.Client(api_key=re.sub(r"\s+", "", (api_key or "").strip()))
    saved = []

    is_news_mode = "한국어 인터넷 뉴스" in selected_style_name
    
    # 🔥 실시간으로 오늘 날짜 추출 (예: 2026년 3월 20일)
    current_date_str = datetime.datetime.now().strftime("%Y년 %m월 %d일")

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set(): break
        
        detected_vips = [vip for vip in VIP_KEYWORDS if vip in scene_text]
        if is_news_mode:
            casting_directive = "MAIN SUBJECT: Create a compelling PRESS PHOTO representing the core event described in the scene, accompanied by a strong news headline."
            _log(f"[장면 {i}/{len(scenes)}] 📰 기사 편집 모드: 최신 날짜({current_date_str}) 반영 및 헤드라인 추출 중...")
        elif detected_vips:
            vip_names = ", ".join(detected_vips)
            casting_directive = f"MAIN SUBJECT: {vip_names}. You MUST draw these specific famous human figures prominently."
            _log(f"[장면 {i}/{len(scenes)}] 👤 인물 감지됨 ({vip_names}) -> 대본 중심 연출...")
        else:
            casting_directive = "MAIN SUBJECT: Focus purely on visualizing the events, concepts, and objects described in the current scene text. ABSOLUTELY NO HUMANS UNLESS EXPLICITLY MENTIONED IN THE SCENE."
            _log(f"[장면 {i}/{len(scenes)}] 📝 대본 감지 -> 대본 내용 100% 반영 연출...")
        
        try:
            prompt_parts = []
            if reference_image_path and os.path.exists(reference_image_path):
                ref_img = Image.open(reference_image_path)
                prompt_parts.append(ref_img)
                style_instruction = "Please MIMIC THE ARTISTIC STYLE (Lighting, Color palette, overall vibe) of the provided reference image."
            else:
                style_instruction = f"STYLE: {style_prompt}"

            text_restriction = ""
            if not is_news_mode:
                text_restriction = "[STRICT RESTRICTIONS - ZERO TEXT]\n- EXPRESS THROUGH VISUALS ONLY (actions, expressions, lighting, composition).\n- ABSOLUTELY NO TEXT: Do not generate ANY text, words, letters, numbers, speech bubbles, labels, or captions anywhere in the image."
            else:
                # 🔥 오늘 날짜(current_date_str)를 AI에게 강제로 주입합니다.
                text_restriction = f"""[JOURNALISTIC REWRITE REQUIRED - KOREAN TEXT ONLY]
- DO NOT literally copy and paste the raw scene text into the image.
- Act as a professional financial news editor: Analyze the scene text, extract the core dramatic message, and write a realistic, catchy NEWS HEADLINE in Korean (Hangul).
- Write realistic Korean body text (기사 본문) that expands on the headline.
- Include standard Korean news UI text (e.g., '[단독]', '[속보]', reporter name).
- 📅 MANDATORY DATE: You MUST set the article's publish date / timestamp exactly to "{current_date_str}".
- ALL visible text MUST be in highly legible Korean (Hangul)."""

            final_prompt = f"""
[ACTION REQUIRED: DRAW THIS EXACT SCENE]
Read the following scene description carefully and make it the absolute main focus of your illustration/webpage. Visualize the actions, emotions, and situations described here:
"{scene_text}"

[SCENE CASTING & SUBJECT]
{casting_directive}

[MOOD & CONTEXT]
- Base: A single, high-quality image in a 16:9 aspect ratio.
- {style_instruction}

{text_restriction}
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )
            
            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                _log(f"❌ 실패 (장면 {i}): 이미지 데이터 없음")
                continue

            final_img = Image.open(io.BytesIO(img_data))
            filename = f"scene_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue
        time.sleep(0.3) 
    return saved


# ==================================================
# 팝아트
# ==================================================
_CSTYLES_gen_popart = {
    "💥 강렬한 대본 맞춤형 팝아트 (Story-Driven Pop Art)": (
        "CRITICAL DIRECTIVE: Do NOT draw generic pop-art characters (like random crying women or comic tropes). You MUST accurately visualize the specific subjects, actions, and objects from the SCENE DESCRIPTION. "
        "Art Style: Apply a retro Pop Art aesthetic (like classic comic books) ONLY as a visual filter. Use bold black outlines, Ben-Day dots (halftone texture), and strong primary colors (reds, yellows, blues). "
        "Atmosphere: Dramatic and visually striking, but 100% faithful to the story in the text."
    ),
    "📰 한국어 인터넷 뉴스 캡처 (Korean Full-Screen News)": (
        "Art Style: **A flat, full-screen digital screenshot of a modern KOREAN financial news website. ABSOLUTELY NO computer monitors, no bezels, no physical devices around it. The webpage interface MUST fill the entire canvas from edge to edge.** "
        "Key Visuals: **The layout MUST include a bold, large, dramatic news HEADLINE in KOREAN (Hangul) summarizing the scene. Below the headline, include a realistic 'Press Photo'. Include standard Korean webpage UI elements (like '뉴스', '증권', reporter name, timestamp) and columns of body text simulating a real Korean article layout.** "
        "Atmosphere: Authoritative, breaking news, journalistic, and highly realistic."
    ),
    "느와르 감성 2D 일러스트 (Noir 2D Webtoon)": (
        "Art Style: **High-quality 2D Korean webtoon / graphic novel illustration. Detailed hand-drawn colored pencil and watercolor style. ABSOLUTELY NO 3D rendering, NO hyper-realism.** "
        "Key Visuals: **Cinematic NOIR atmosphere, dramatic heavy shadows, low-key lighting, and a moody, serious color palette, but maintaining the charming 2D drawn aesthetic.** "
        "Background: **Highly detailed 2D illustrated environment.** "
        "Atmosphere: Dark, serious, high-stakes, dramatic, but artistically beautiful."
    )
}

def gen_popart(api_key: str, full_script: str, scenes: List[str], out_dir: str, style_prompt: str, selected_style_name: str, reference_image_path: Optional[str] = None, cancel_event: Optional[threading.Event] = None, log_fn=None) -> List[str]:
    if not HAS_GENAI: raise Exception("google-generativeai 라이브러리가 필요합니다.")
    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn: log_fn(msg)

    _client = genai.Client(api_key=re.sub(r"\s+", "", (api_key or "").strip()))
    saved = []

    is_news_mode = "한국어 인터넷 뉴스" in selected_style_name
    
    # 🔥 실시간으로 오늘 날짜 추출 (예: 2026년 3월 20일)
    current_date_str = datetime.datetime.now().strftime("%Y년 %m월 %d일")

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set(): break
        
        detected_vips = [vip for vip in VIP_KEYWORDS if vip in scene_text]
        if is_news_mode:
            casting_directive = "MAIN SUBJECT: Create a compelling PRESS PHOTO representing the core event described in the scene, accompanied by a strong news headline."
            _log(f"[장면 {i}/{len(scenes)}] 📰 기사 편집 모드: 최신 날짜({current_date_str}) 반영 및 헤드라인 추출 중...")
        elif detected_vips:
            vip_names = ", ".join(detected_vips)
            casting_directive = f"MAIN SUBJECT: {vip_names}. You MUST draw these specific famous human figures prominently."
            _log(f"[장면 {i}/{len(scenes)}] 👤 인물 감지됨 ({vip_names}) -> 대본 중심 연출...")
        else:
            casting_directive = "MAIN SUBJECT: Focus purely on visualizing the events, concepts, and objects described in the current scene text. ABSOLUTELY NO HUMANS UNLESS EXPLICITLY MENTIONED IN THE SCENE."
            _log(f"[장면 {i}/{len(scenes)}] 📝 대본 감지 -> 대본 내용 100% 반영 연출...")
        
        try:
            prompt_parts = []
            if reference_image_path and os.path.exists(reference_image_path):
                ref_img = Image.open(reference_image_path)
                prompt_parts.append(ref_img)
                style_instruction = "Please MIMIC THE ARTISTIC STYLE (Lighting, Color palette, overall vibe) of the provided reference image."
            else:
                style_instruction = f"STYLE: {style_prompt}"

            text_restriction = ""
            if not is_news_mode:
                text_restriction = "[STRICT RESTRICTIONS - ZERO TEXT]\n- EXPRESS THROUGH VISUALS ONLY (actions, expressions, lighting, composition).\n- ABSOLUTELY NO TEXT: Do not generate ANY text, words, letters, numbers, speech bubbles, labels, or captions anywhere in the image."
            else:
                text_restriction = f"""[JOURNALISTIC REWRITE REQUIRED - KOREAN TEXT ONLY]
- DO NOT literally copy and paste the raw scene text into the image.
- Act as a professional financial news editor: Analyze the scene text, extract the core dramatic message, and write a realistic, catchy NEWS HEADLINE in Korean (Hangul).
- Write realistic Korean body text (기사 본문) that expands on the headline.
- Include standard Korean news UI text (e.g., '[단독]', '[속보]', reporter name).
- 📅 MANDATORY DATE: You MUST set the article's publish date / timestamp exactly to "{current_date_str}".
- ALL visible text MUST be in highly legible Korean (Hangul)."""

            # 🔥 프롬프트 최상단에 대본 우선순위 족쇄 추가
            final_prompt = f"""
[STRICT RULE: SCENE CONTENT IS THE ABSOLUTE PRIORITY]
Your primary job is to visualize the exact events, characters, and actions described in the SCENE DESCRIPTION below. The art style is secondary and must only be applied as a filter to the subjects in the scene. DO NOT invent random scenes or generic characters that are not in the text.

[SCENE DESCRIPTION (DRAW THIS EXACTLY)]:
"{scene_text}"

[SCENE CASTING & SUBJECT]
{casting_directive}

[MOOD, CONTEXT & ART STYLE]
- Base: A single, high-quality image in a 16:9 aspect ratio.
- {style_instruction}

{text_restriction}
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )
            
            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                _log(f"❌ 실패 (장면 {i}): 이미지 데이터 없음")
                continue

            final_img = Image.open(io.BytesIO(img_data))
            filename = f"scene_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue
        time.sleep(0.3) 
    return saved


# ==================================================
# 마인크래프트
# ==================================================
_STYLE_gen_minecraft = (
    "Art Style: **A high-quality, intricate 3D Voxel art illustration, perfectly mimicking the look of specialized Minecraft models and environments.** "
    "Environment: Sophisticated, moody cinematic lighting with dramatic high-contrast shadows and rich, deep cinematic colors. Everything from landscapes to objects must be built from perfect blocky structures with crisp pixel textures. "
    "Vibe: High-stakes drama, authoritative, dark and cinematic. NO realistic details, ONLY pure blocky 3D rendering."
)

def gen_minecraft(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_paths: List[str] = [],
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI: raise Exception("google-generativeai가 설치되지 않았습니다.")
    if not Image: raise Exception("Pillow가 설치되지 않았습니다.")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn: log_fn(msg)

    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    if not api_key: raise Exception("Gemini API Key가 비었습니다.")
    
    _client = genai.Client(api_key=api_key)
    _client = genai.Client(api_key=api_key)

    saved = []

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 컷 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 분석 및 3D 복셀 일러스트 생성 중...")
        
        try:
            prompt_parts = []
            
            # 🔥 대본에 VIP 인물이 있는지 확인 (캐릭터 등판 유무 결정)
            detected_vips = [vip for vip in VIP_KEYWORDS if vip in scene_text]
            
            if detected_vips:
                vip_names = ", ".join(detected_vips)
                _log(f"  └ 👤 대본 내 인물 감지됨: {vip_names} -> 캐릭터 렌더링 활성화")
                character_directive = f"- VIP CHARACTER FOCUS: The scene mentions {vip_names}. Render them prominently as a high-quality 3D Minecraft voxel figure wearing a suit or appropriate attire, acting out the scene."
                no_character_restriction = ""
                
                # 레퍼런스 이미지가 있을 경우 캐릭터 복제 지시
                if reference_image_paths:
                    for p in reference_image_paths:
                        if os.path.exists(p):
                            prompt_parts.append(Image.open(p))
                    style_instruction = (
                        "CRITICAL DIRECTIVE: Use the reference image to extract BOTH the 3D cinematic voxel ART STYLE AND the exact character design. "
                        "Clone the character's blocky appearance and place them in the new context of the scene description."
                    )
                else:
                    style_instruction = f"STYLE: {_STYLE_gen_minecraft}"
                    
            else:
                _log(f"  └ ⛰️ 대본 내 인물 없음 -> 배경/사물 렌더링(캐릭터 금지)")
                character_directive = "- ENVIRONMENT & OBJECT FOCUS: Focus purely on visualizing the landscape, architecture, or objects (e.g., blocky charts, gold coins, red/green candles, buildings) described in the scene."
                no_character_restriction = "- CRITICAL RESTRICTION: DO NOT DRAW ANY CHARACTERS OR HUMANS in this scene. Focus ONLY on the environment and objects."
                
                # 레퍼런스 이미지가 있을 경우 '화풍'만 가져오고 캐릭터는 무시
                if reference_image_paths:
                    for p in reference_image_paths:
                        if os.path.exists(p):
                            prompt_parts.append(Image.open(p))
                    style_instruction = (
                        "CRITICAL DIRECTIVE: Use the reference image STRICTLY to extract the 3D cinematic voxel ART STYLE, moody lighting, and blocky textures. "
                        "IGNORE the character in the reference image. Apply this 3D block style ONLY to the environment and objects described below."
                    )
                else:
                    style_instruction = f"STYLE: {_STYLE_gen_minecraft}"

            # 🔥 최종 프롬프트 조합
            final_prompt = f"""
Create a single, high-quality 16:9 3D Voxel illustration, drawn in an authoritative cinematic art style.

[Visual Storytelling Directive]
- CORE CONCEPT: Read the SCENE DESCRIPTION carefully to understand the context.
{character_directive}
- MOOD & LIGHTING: Use Moody cinematic lighting with dramatic high-contrast shadows. The world must look like a high-end 3D block game.
- TEXT DECISION: If adding short KOREAN text (like a pixelated speech bubble or floating block text) helps convey the script's message, add it. Otherwise, leave it text-free. ABSOLUTELY NO ENGLISH.
{no_character_restriction}

SCENE DESCRIPTION: "{scene_text}"

[Art Style]
{style_instruction}
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            final_img = Image.open(io.BytesIO(img_data))
            filename = f"3d_cinematic_voxel_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3)

    return saved


# ==================================================
# 세포
# ==================================================
_STYLE_gen_cells = (
    "Art Style: **Detailed hand-drawn colored pencil and watercolor sketch, high-quality Korean graphic novel/webtoon style**. "
    "Key Visuals: Warm, nostalgic, slightly muted vintage color palette. Fine pencil hatching and shading. "
    "Characters (STRICT RULE): **NO NORMAL HUMANS. The ONLY characters in the scene must be extremely cute, tiny, round, blob-like mini mascot creatures (like adorable 'emotion cells' or little spirits).** They have big eyes, rosy cheeks, and wear cute little hoods or hats. "
    "Backgrounds: Highly detailed everyday environments (like cozy wooden desks, giant keyboards, bookshelves) depicted from a macro perspective so the creatures look very tiny interacting with giant objects. Warm natural lighting. "
    "Language: **ALL TEXT MUST BE STRICTLY IN KOREAN (한국어). ABSOLUTELY NO ENGLISH.**"
)

def gen_cells(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 설치되지 않았습니다.")
    if not Image:
        raise Exception("Pillow가 설치되지 않았습니다.")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    if not api_key:
        raise Exception("Gemini API Key가 비었습니다.")
    
    _client = genai.Client(api_key=api_key)
    # 모델 초기화 (안전 설정 포함)
    _client = genai.Client(api_key=api_key)

    saved = []

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 컷 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 분석 및 생성 중...")
        
        try:
            prompt_parts = []
            
            # 오직 GUI에서 직접 선택한 레퍼런스 이미지만 참고합니다.
            if reference_image_path and os.path.exists(reference_image_path):
                ref_img = Image.open(reference_image_path)
                prompt_parts.append(ref_img)
                style_instruction = (
                    "Please EXACTLY MIMIC the warm, vintage colored pencil and watercolor webtoon style "
                    "of the provided reference image. Match the line weight, shading, and color palette perfectly."
                )
            else:
                style_instruction = f"STYLE: {_STYLE_gen_cells}"

            # [핵심 업데이트] 인간 제거, 미니 세포 전용, 말풍선 금지/텍스트 환경 배치
            final_prompt = f"""
Create a single, high-quality 16:9 illustration panel.

[Visual Storytelling & Text Rules]
- CORE CONCEPT: Read the SCENE DESCRIPTION carefully and extract the core meaning.
- NO HUMANS: **DO NOT DRAW ANY NORMAL, HUMAN-SIZED PEOPLE.**
- MINI CHARACTERS ONLY: Express the scene entirely through the actions of tiny, cute, blob-like mascot creatures (cells/spirits) interacting with their environment or macro-sized objects (like giant smartphones, giant laptops, giant pens, or a giant desk).
- NO EXCESSIVE BUBBLES: Do not fill the image with speech bubbles. Use visual metaphors (actions, expressions) instead of dialogue.
- MINIMAL TEXT: If text is absolutely necessary, extract ONLY 1 to 3 short keywords. Integrate these keywords naturally into the environment (e.g., written on the giant laptop screen, on a note the tiny cells are holding, or on a blackboard in their mini-village) rather than using floating speech bubbles.
- LANGUAGE: **ALL TEXT MUST BE IN KOREAN (한국어). ABSOLUTELY NO ENGLISH.**

SCENE DESCRIPTION: {scene_text}

[Art Style]
{style_instruction}
Create a high-quality, detailed Korean graphic novel illustration. Warm lighting, vintage vibe. Focus on the tiny cute creatures acting out the scene without drawing normal humans.
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            
            _log(f"  └ 🎨 미니 캐릭터 묘사 및 이미지 생성 중...")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            # 저장
            final_img = Image.open(io.BytesIO(img_data))
            filename = f"comic_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3) # API Rate limit 방지

    return saved


# ==================================================
# 시바짤방
# ==================================================
_STYLE_gen_meme = (
    "Art Style: **Intentionally low-quality, simple MS Paint style, internet meme vibe (그림판 짤방 느낌).** "
    "Key Visuals: Very simple flat colors, unpolished and slightly jagged lines, stick-figure or extremely basic cartoon characters. "
    "Vibe: B-grade comedy, goofy aesthetics, and humorous exaggerations. "
    "Detail Level: KEEP IT EXTREMELY SIMPLE. It should look like it was quickly drawn in Microsoft Paint by an amateur for a funny internet post. NO realistic details, NO complex shading."
)

def gen_meme(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 설치되지 않았습니다.")
    if not Image:
        raise Exception("Pillow가 설치되지 않았습니다.")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    if not api_key:
        raise Exception("Gemini API Key가 비었습니다.")
    
    _client = genai.Client(api_key=api_key)
    # 모델 초기화 (안전 설정 포함)
    _client = genai.Client(api_key=api_key)

    saved = []

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 컷 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 분석 및 생성 중...")
        
        try:
            prompt_parts = []
            
            # 레퍼런스 이미지가 있을 경우, 짤방 스타일을 참고하도록 지시 변경
            if reference_image_path and os.path.exists(reference_image_path):
                ref_img = Image.open(reference_image_path)
                prompt_parts.append(ref_img)
                style_instruction = (
                    "Please EXACTLY MIMIC the low-quality, funny, and simple meme style "
                    "of the provided reference image. Keep the art very basic and unpolished, just like a quick MS Paint sketch."
                )
            else:
                style_instruction = f"STYLE: {_STYLE_gen_meme}"

            # [핵심 업데이트] AI 텍스트 자율 판단 로직 도입
            final_prompt = f"""
Create a single, high-quality 16:9 image, BUT drawn in an intentionally low-quality, funny internet meme style (그림판 짤방).

[Visual Storytelling & AI Text Rules]
- CORE CONCEPT: Read the SCENE DESCRIPTION carefully to understand the humorous or dramatic situation.
- DRAWING VIBE: It must look like a popular internet meme. Use funny, exaggerated, and derpy characters. No complex backgrounds unless needed for the joke.
- TEXT DECISION (YOUR CHOICE): You are an intelligent AI. Analyze the scene and decide for yourself: "Would adding text make this image funnier or easier to understand?" 
- IF YES (TEXT NEEDED): Add short, punchy text naturally into the scene (e.g., as a speech bubble, text floating above a character, or written on a sign).
- IF NO (NO TEXT NEEDED): Do not include any text. Let the silly visual tell the story alone.
- LANGUAGE RULE: IF you decide to add text, **IT MUST BE STRICTLY IN KOREAN (한국어). ABSOLUTELY NO ENGLISH.**

SCENE DESCRIPTION: {scene_text}

[Art Style]
{style_instruction}
Create a funny, simple, B-grade meme illustration. Let the humor shine through the simplicity!
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            
            _log(f"  └ 🎨 짤방 스타일 묘사 및 이미지 생성 중 (텍스트 AI 자율 판단)...")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            # 저장
            final_img = Image.open(io.BytesIO(img_data))
            filename = f"meme_cut_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3) # API Rate limit 방지

    return saved


# ==================================================
# 수채화
# ==================================================
_STYLE_gen_watercolor = (
    "Art Style: **Breathtakingly beautiful, masterpiece-level fine art watercolor painting. "
    "Ethereal, deeply emotional, and highly artistic brushstrokes with a rich, translucent, and luminous color palette. "
    "It should look like an award-winning gallery painting, incredibly poetic and delicate. Absolutely NO 3D CGI or harsh digital lines.**"
)

def gen_watercolor(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_paths: List[str] = [],
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI: raise Exception("google-generativeai가 설치되지 않았습니다.")
    if not Image: raise Exception("Pillow가 설치되지 않았습니다.")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn: log_fn(msg)

    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    if not api_key: raise Exception("Gemini API Key가 비었습니다.")
    
    _client = genai.Client(api_key=api_key)
    _client = genai.Client(api_key=api_key)

    saved = []

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 컷 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 분석 및 극감성 수채화 씬 연출 중...")
        
        try:
            prompt_parts = []
            
            # 대본에 VIP 인물이 있는지 확인
            detected_vips = [vip for vip in VIP_KEYWORDS if vip in scene_text]
            
            if detected_vips:
                vip_names = ", ".join(detected_vips)
                _log(f"  └ 👤 대본 내 VIP 감지됨: {vip_names} -> 수채화풍 VIP 연출")
                
                character_directive = (
                    f"- SCENE ACTION & ENVIRONMENT: Read the SCENE DESCRIPTION very carefully. "
                    f"Place the characters in the exact setting and situation described in the text, rendered beautifully in watercolor. "
                    f"The scene mentions VIPs ({vip_names}). Render them elegantly in this artistic watercolor style."
                )
                
            else:
                _log(f"  └ 🎨 대본 내 VIP 없음 -> 대본 상황에 맞춘 수채화 풍경/사물 연출")
                
                character_directive = (
                    f"- SCENE ACTION & ENVIRONMENT: Read the SCENE DESCRIPTION very carefully. "
                    f"Visualize the setting, mood, and situation described in the text using beautiful watercolor techniques. "
                )

            # 레퍼런스 이미지 화풍 추출
            if reference_image_paths:
                for p in reference_image_paths:
                    if os.path.exists(p):
                        prompt_parts.append(Image.open(p))
                style_instruction = (
                    "CRITICAL DIRECTIVE: Use the reference image to understand the general mood or subject, but you MUST strictly apply a BREATHTAKING WATERCOLOR PAINTING STYLE. "
                    "The final image must look like a high-end watercolor artwork with soft edges, fluid colors, and ethereal lighting."
                )
            else:
                style_instruction = f"STYLE: {_STYLE_gen_watercolor}"

            # 🔥 텍스트 극소량 제어 프롬프트 조합
            final_prompt = f"""
Create a single, high-quality 16:9 beautiful watercolor illustration.

[Visual Storytelling Directive]
{character_directive}
- TEXT DECISION (USE EXTREMELY SPARINGLY): Do NOT clutter the beautiful art with text. 80% of your generated scenes should have NO TEXT AT ALL. Use text ONLY IF it provides a critical emotional punch or climax to the scene. If you must add text, keep it to 1-3 short KOREAN words maximum, written elegantly as if painted with a watercolor brush so it blends seamlessly into the artistic background. ABSOLUTELY NO ENGLISH.

SCENE DESCRIPTION: "{scene_text}"

[Art Style]
{style_instruction}
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            final_img = Image.open(io.BytesIO(img_data))
            filename = f"masterpiece_watercolor_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3)

    return saved


# ==================================================
# 코인가이드
# ==================================================
_STYLE_gen_coinguide = (
    "Art Style: **High-end 3D CGI animation style (similar to cute Pixar or Disney movies), highly detailed and expressive. Absolutely NO Minecraft, NO voxels, and NO blocky pixelated shapes. Smooth surfaces with rich, highly realistic textures (leather, metal, fabric).** "
    "Environment & Vibe: Sophisticated, moody cinematic lighting with dramatic high-contrast shadows. The colors should be deep and rich, conveying dramatic storytelling but with a touch of appealing charm."
)

def gen_coinguide(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_paths: List[str] = [],
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI: raise Exception("google-generativeai가 설치되지 않았습니다.")
    if not Image: raise Exception("Pillow가 설치되지 않았습니다.")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn: log_fn(msg)

    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    if not api_key: raise Exception("Gemini API Key가 비었습니다.")
    
    _client = genai.Client(api_key=api_key)
    _client = genai.Client(api_key=api_key)

    saved = []

    # 🔥 메인 캐릭터 외형 정의 (텍스트 제거 & 귀여움 유지)
    MAIN_CHARACTER_DESIGN = (
        "The main protagonist is a high-quality 3D CGI anthropomorphic character. "
        "Their head is a highly detailed, thick, vintage green leather-bound book. "
        "CRITICAL: DO NOT write any text on the front cover of the book face. Keep the green leather clean and blank. "
        "The character MUST look incredibly CUTE, LOVABLE, and ADORABLE, much like a charming Pixar character. "
        "The blank book face has very large, sparkling, adorable eyes, cute rosy cheeks, and a sweet, friendly smile. "
        "The character has slightly rounder, softer proportions to enhance cuteness, while still wearing a sharp green suit with white gloves."
    )

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 컷 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 분석 및 귀여운 다이내믹 씬 연출 중...")
        
        try:
            prompt_parts = []
            
            # 대본에 VIP 인물이 있는지 확인
            detected_vips = [vip for vip in VIP_KEYWORDS if vip in scene_text]
            
            if detected_vips:
                vip_names = ", ".join(detected_vips)
                _log(f"  └ 👤 대본 내 VIP 감지됨: {vip_names} -> 텍스트없는 귀요미 캐릭터 + VIP 상호작용")
                
                character_directive = (
                    f"- PROTAGONIST DESIGN: {MAIN_CHARACTER_DESIGN}\n"
                    f"- SCENE ACTION & ENVIRONMENT: Read the SCENE DESCRIPTION very carefully. "
                    f"Place the protagonist in the exact setting, mood, and situation described in the text. "
                    f"They must perform the actions and show the emotions dictated by the scene, maintaining their cute appeal. "
                    f"The scene also mentions VIPs ({vip_names}). Render the protagonist interacting with or reacting to these VIPs as described."
                )
                
            else:
                _log(f"  └ 📚 대본 내 VIP 없음 -> 대본 상황에 맞춘 귀요미 캐릭터 단독 연기")
                
                character_directive = (
                    f"- PROTAGONIST DESIGN: {MAIN_CHARACTER_DESIGN}\n"
                    f"- SCENE ACTION & ENVIRONMENT: Read the SCENE DESCRIPTION very carefully. "
                    f"Place the protagonist in the exact setting, mood, and situation described in the text. "
                    f"They must perform the actions and show the emotions dictated by the scene, maintaining their adorable charm. DO NOT default to a radio studio unless the script implies it."
                )

            # 레퍼런스 이미지 화풍 추출
            if reference_image_paths:
                for p in reference_image_paths:
                    if os.path.exists(p):
                        prompt_parts.append(Image.open(p))
                style_instruction = (
                    "CRITICAL DIRECTIVE: Use the reference image strictly to extract the high-end 3D CGI ART STYLE, lighting, and general color palette. "
                    "However, IGNORE the exact face or proportions of the reference character. Make sure the generated character looks MUCH CUTER (larger sparkling eyes, softer features) and HAS NO TEXT on its face/cover as described above. "
                    "The character's pose, emotion, and environment MUST be defined by the SCENE DESCRIPTION below."
                )
            else:
                style_instruction = f"STYLE: {_STYLE_gen_coinguide}"

            # 최종 프롬프트 조합
            final_prompt = f"""
Create a single, high-quality 16:9 3D CGI illustration, drawn in an authoritative yet charming cinematic art style. DO NOT USE MINECRAFT, VOXEL, OR PIXEL STYLES. Ensure all edges are smooth and textures are highly realistic.

[Visual Storytelling Directive]
{character_directive}
- TEXT DECISION: DO NOT put any text on the protagonist's face/cover. Keep it as clean leather. If adding short KOREAN text elsewhere (like on a computer monitor or a signpost in the background) helps convey the script's message, integrate it naturally. ABSOLUTELY NO ENGLISH text.

SCENE DESCRIPTION: "{scene_text}"

[Art Style]
{style_instruction}
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            final_img = Image.open(io.BytesIO(img_data))
            filename = f"3d_cinematic_cute_notext_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3)

    return saved


# ==================================================
# 칠판
# ==================================================
_STYLE_gen_chalkboard = (
    "Art Style: **Refined Chalkboard Drawing / Professional Blackboard Sketch style**. "
    "Key Visuals: **Fine white chalk lines**. "
    "Characters: (If any) **Stylized, functional 2D outline figures**, prioritizing diagrammatic representation over cuteness. Minimalist, clear, and schematic 'sketchy' feel. "
    "Background STRICT RULES: **The ENTIRE canvas MUST be filled edge-to-edge with a uniform, flat, solid dark green chalkboard color. DO NOT draw wooden frames. DO NOT draw chalkboard edges, perspective lines, erasers, or chalk pieces. Only the flat green slate.** "
    "NO Speech Bubbles: **DO NOT use speech bubbles or thought bubbles.** "
    "Text integration: Text should appear as captions, labels, or part of a diagram on the board. "
    "Atmosphere: Academic, professional, and clear; emphasizing information presentation."
)

def gen_chalkboard(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 설치되지 않았습니다.")
    if not Image:
        raise Exception("Pillow가 설치되지 않았습니다.")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    if not api_key:
        raise Exception("Gemini API Key가 비었습니다.")
    
    _client = genai.Client(api_key=api_key)
    # 모델 초기화 (안전 설정 포함)
    _client = genai.Client(api_key=api_key)

    saved = []

    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 컷 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 분석 및 생성 중...")
        
        try:
            prompt_parts = []
            
            # 오직 GUI에서 직접 선택한 레퍼런스 이미지만 참고합니다.
            if reference_image_path and os.path.exists(reference_image_path):
                ref_img = Image.open(reference_image_path)
                prompt_parts.append(ref_img)
                style_instruction = (
                    "Please MIMIC THE ARTISTIC STYLE (Line weight, Color palette, Schematic quality) "
                    "of the provided reference image. Ensure the visual logic and diagrammatic style are consistent."
                )
            else:
                style_instruction = f"STYLE: {_STYLE_gen_chalkboard}"

            # [핵심 업데이트] 텍스트 한국어 강제 (Korean ONLY, NO English)
            final_prompt = f"""
Create a single, high-quality 16:9 refined chalkboard educational drawing panel.

[Structural & Content Rules]
- Content: Read the SCENE DESCRIPTION below and draw the schematic/visual story.
- BACKGROUND STRICT RULE: **The 16:9 canvas MUST be completely filled edge-to-edge with a flat, solid dark green chalkboard color. ABSOLUTELY NO wooden borders, NO edges, NO perspective, NO erasers, and NO chalk sticks drawn.** Just the flat green background.
- TEXT EXTRACTION: You must identify 1 to 3 MOST IMPORTANT short key phrases from the scene description. **These extracted phrases MUST be in Korean (한국어).**
- TEXT TO INCLUDE: Draw ONLY those extracted key phrases directly onto the chalkboard as hand-drawn captions or diagram labels. **ALL TEXT WRITTEN ON THE CHALKBOARD MUST BE IN KOREAN (한국어). ABSOLUTELY NO ENGLISH.**
- **NO Speech Bubbles.**
- **NO Long Sentences.**
- **No specific protagonist.** Keep it abstract and schematic.

SCENE DESCRIPTION: {scene_text}

[Art Style]
{style_instruction}
Clear, crisp white chalk lines on STRICTLY FLAT SOLID DARK GREEN background. Academic, schematic, diagram vibe.
""".strip()

            prompt_parts.append(f"Draw this image: {final_prompt}")
            
            _log(f"  └ 🎨 이미지 생성 중...")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

            img_data = None
            if getattr(response, "parts", None):
                for part in response.parts:
                    if getattr(part, "inline_data", None):
                        img_data = part.inline_data.data
                        break

            if not img_data:
                err = getattr(response, "text", None) or "이미지 데이터 없음"
                _log(f"❌ 실패 (장면 {i}): {err}")
                continue

            # 저장
            final_img = Image.open(io.BytesIO(img_data))
            filename = f"comic_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, filename)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장 완료: {save_path}")

        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue

        time.sleep(0.3) # API Rate limit 방지

    return saved


# ==================================================
# 찰흙
# ==================================================
_STYLE_gen_clay = (
    "Art Style: **Claymation / Plasticine Animation Style** (Stop-motion feel). "
    "Key Visuals: **Soft clay texture**, visible hand-sculpted details, tiny fingerprints, semi-glossy finish. "
    "Characters: **Cute 3D clay figures**. Round, plump, and chunky shapes with a high sense of volume. "
    "Features: Googly eyes or simple bead eyes, thick limbs, expressive mouth shapes made of clay pieces. "
    "Atmosphere: Playful, tactile, and warm studio lighting with soft shadows. "
    "Background: Simple miniature sets made of clay or cardboard-like textures. "
    "IMPORTANT: Make everything look like it was physically handmade from modeling clay."
)

def gen_clay(

    api_key: str,

    scenes: List[str],

    out_dir: str,

    reference_image_path: Optional[str] = None,

    cancel_event: Optional[threading.Event] = None,

    log_fn=None,

) -> List[str]:

    if not HAS_GENAI:

        raise Exception("google-generativeai가 설치되지 않았습니다. pip install google-generativeai")

    if not Image:

        raise Exception("Pillow가 설치되지 않았습니다. pip install pillow")



    ensure_dir(out_dir)



    def _log(msg: str):

        if log_fn:

            log_fn(msg)



    # API 키 정제 (키 파일에 한글/설명 문구 섞이면 gRPC 'Illegal header value'가 터질 수 있음)

    api_key = (api_key or "").strip()

    api_key = re.sub(r"\s+", "", api_key)

    if not api_key:

        raise Exception("Gemini API Key가 비었습니다. api_key.txt 또는 환경변수 GEMINI_API_KEY를 확인하세요.")

    if any((ord(c) < 32) or (ord(c) > 126) for c in api_key):

        raise Exception("Gemini API Key에 비 ASCII 문자가 섞여있습니다. api_key.txt에는 키만 한 줄로 넣어주세요.")

    _client = genai.Client(api_key=api_key)
    _client = genai.Client(api_key=api_key)



    saved = []

    for i, scene_text in enumerate(scenes, start=1):

        if cancel_event and cancel_event.is_set():

            _log("⛔ 취소됨: 코믹스 생성 중단")

            break



        _log(f"[장면 {i}/{len(scenes)}] 생성 중...")

        try:

            prompt_parts = []

            style_instruction = ""



            if reference_image_path and os.path.exists(reference_image_path):

                ref_img = Image.open(reference_image_path)

                prompt_parts.append(ref_img)

                style_instruction = (

                    "Please MIMIC THE ARTISTIC STYLE (Line weight, Color palette, Character design) "

                    "of the provided reference image. Ensure consistency."

                )

            else:

                style_instruction = f"STYLE: {_STYLE_gen_clay}"



            final_prompt = f"""

Create a single, high-quality 16:9 cartoon panel.



[Content & Text Rules]

- Read the SCENE DESCRIPTION.

- Identify ONLY the most IMPORTANT short key phrase(s) (few words) and draw only those into the image.

- Do not draw long paragraphs.

- If Korean text is used, draw it in a natural hand-drawn comic font.



SCENE DESCRIPTION: {scene_text}



[Style & Design Rules]

{style_instruction}

- Flat, funny, hand-drawn cartoon panel.

- Clear visual storytelling.

- Simple cute characters with exaggerated expressions.

""".strip()



            prompt_parts.append(f"Draw this image: {final_prompt}")

            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )



            img_data = None

            if getattr(response, "parts", None):

                for part in response.parts:

                    if getattr(part, "inline_data", None):

                        img_data = part.inline_data.data

                        break



            if not img_data:

                err = getattr(response, "text", None) or "이미지 데이터 없음"

                _log(f"❌ 실패 (장면 {i}): {err}")

                continue



            final_img = Image.open(io.BytesIO(img_data))

            filename = f"comic_{i:03d}_{now_ts()}.png"

            save_path = os.path.join(out_dir, filename)

            final_img.save(save_path)

            saved.append(save_path)

            _log(f"✅ 저장: {save_path}")



        except Exception as e:

            _log(f"❌ 에러 (장면 {i}): {e}")

            continue



        time.sleep(0.3)



    return saved


# ==================================================
# 숏츠
# ==================================================
_STYLE_gen_shorts = (
    "Art Style: **Indie Comic / Modern Western Cartoon Style** (Generic, Hand-drawn feel). "
    "Key Visuals: **Thin, wobbly hand-drawn lines**, flat pastel and vibrant colors, very simple shading. "
    "Characters: **Create ORIGINAL characters**. Use simple geometric shapes (circle heads, bean-shaped bodies). "
    "Features: Dot eyes or simple expressive eyes, **noodle-like rubbery limbs** (rubbery hose animation style), exaggerated expressions. "
    "Atmosphere: Humorous, whimsical, quirky, and lighthearted 2D vector art. "
    "IMPORTANT: Do NOT copy specific characters from TV shows like Adventure Time. Create unique designs."
)

def gen_shorts(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    is_shorts: bool = False,  # 쇼츠 여부 추가
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 설치되지 않았습니다. pip install google-generativeai")
    if not Image:
        raise Exception("Pillow가 설치되지 않았습니다. pip install pillow")

    ensure_dir(out_dir)

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    # API 키 정제
    api_key = (api_key or "").strip()
    api_key = re.sub(r"\s+", "", api_key)
    if not api_key:
        raise Exception("Gemini API Key가 비었습니다. api_key.txt 또는 환경변수 GEMINI_API_KEY를 확인하세요.")
    if any((ord(c) < 32) or (ord(c) > 126) for c in api_key):
        raise Exception("Gemini API Key에 비 ASCII 문자가 섞여있습니다. api_key.txt에는 키만 한 줄로 넣어주세요.")
    _client = genai.Client(api_key=api_key)
    _client = genai.Client(api_key=api_key)

    # ▼▼▼ 쇼츠 안전지대 / 텍스트 제거 / 테두리 제거 프롬프트 로직 ▼▼▼
    if is_shorts:
        ratio_prompt = "9:16 Vertical (Portrait) full frame aspect ratio"
        orientation = "Vertical, tall composition"
        
        # [변경] 쇼츠용 텍스트 규칙: 텍스트 금지
        text_rules = (
            "[NO TEXT RULE - STRICT]\n"
            "- Do NOT include any text, speech bubbles, thinking bubbles, sound effects, or captions.\n"
            "- The image must be a visual illustration ONLY.\n"
            "- Focus on facial expressions and body language to convey the meaning."
        )

        composition_guide = (
            "[COMPOSITION RULE - YouTube Shorts Safe Zone (CRITICAL)]\n"
            "This image will be used as a YouTube Short. You MUST keep the main content within the central 'safe zone'.\n"
            "- **TOP 15% (Top UI):** Keep this area CLEAR.\n"
            "- **BOTTOM 30% (Title & Description):** Keep this area CLEAR.\n"
            "- **RIGHT 20% (Action Buttons):** Keep this area CLEAR. Focus content towards the left and center.\n"
            "- **CENTER-MIDDLE AREA:** Place ALL characters and action in this central region.\n"
            "- The outer edges should only contain background elements.\n\n"
            "[NEGATIVE CONSTRAINTS - NO BORDER/NO TEXT]\n"
            "- Do NOT draw a mobile phone bezel, frame, or border.\n"
            "- Do NOT draw a hand holding a phone.\n"
            "- Do NOT draw speech bubbles or text.\n"
            "- The image should fill the entire 9:16 canvas edge-to-edge."
        )
    else:
        # 일반 모드 (텍스트 허용)
        ratio_prompt = "16:9 Horizontal (Landscape) cinematic size"
        orientation = "Horizontal, wide composition"
        
        text_rules = (
            "[Content & Text Rules]\n"
            "- Identify ONLY the most IMPORTANT short key phrase(s) (few words) and draw only those into the image.\n"
            "- Do not draw long paragraphs.\n"
            "- If Korean text is used, draw it in a natural hand-drawn comic font."
        )
        
        composition_guide = "[COMPOSITION RULE]\n- Wide cinematic shot, center the action."
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    saved = []
    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set():
            _log("⛔ 취소됨: 코믹스 생성 중단")
            break

        _log(f"[장면 {i}/{len(scenes)}] 생성 중... ({orientation})")
        
        # ▼▼▼ [재시도 로직] 429 에러 대응 ▼▼▼
        max_retries = 3
        success = False
        
        for attempt in range(max_retries):
            try:
                prompt_parts = []
                style_instruction = ""

                if reference_image_path and os.path.exists(reference_image_path):
                    ref_img = Image.open(reference_image_path)
                    prompt_parts.append(ref_img)
                    style_instruction = (
                        "Please MIMIC THE ARTISTIC STYLE (Line weight, Color palette, Character design) "
                        "of the provided reference image. Ensure consistency."
                    )
                else:
                    style_instruction = f"STYLE: {_STYLE_gen_shorts}"

                final_prompt = f"""
Create a single, high-quality {ratio_prompt} cartoon panel.

{text_rules}

SCENE DESCRIPTION: {scene_text}

[Style & Design Rules]
{style_instruction}
- {orientation}.
- Flat, funny, hand-drawn cartoon panel.
- Clear visual storytelling.
- Simple cute characters with exaggerated expressions.

{composition_guide}
""".strip()

                prompt_parts.append(f"Draw this image: {final_prompt}")
                response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )

                img_data = None
                if getattr(response, "parts", None):
                    for part in response.parts:
                        if getattr(part, "inline_data", None):
                            img_data = part.inline_data.data
                            break

                if not img_data:
                    err = getattr(response, "text", None) or "이미지 데이터 없음"
                    raise Exception(err)

                final_img = Image.open(io.BytesIO(img_data))
                filename = f"comic_{i:03d}_{now_ts()}.png"
                save_path = os.path.join(out_dir, filename)
                final_img.save(save_path)
                saved.append(save_path)
                _log(f"✅ 저장: {save_path}")
                success = True
                break # 성공 시 재시도 루프 탈출

            except Exception as e:
                if "429" in str(e) or "Resource has been exhausted" in str(e):
                    _log(f"⚠️ 과부하(429) 발생! 20초 휴식 후 재시도... ({attempt+1}/{max_retries})")
                    time.sleep(20) # 429 에러는 20초 쉼
                else:
                    _log(f"❌ 에러 (장면 {i}): {e}")
                    break # 다른 에러는 즉시 중단
        
        if success:
            # 429 에러 방지를 위해 기본 쿨타임 증가 (15초)
            _log("⏳ 다음 장면 준비 중 (15초 대기)...")
            time.sleep(15) 

    return saved




# ==================================================
# 📸 인스타 감성 일러스트
# ==================================================

_STYLE_gen_insta = (
    "Art Style: **Soft, dreamy Instagram-worthy illustration**. Key Visuals: Warm pastel color palette "
    "(peach, lavender, sage green, dusty pink), clean minimal line art with gentle watercolor washes. "
    "Aesthetic: Cozy, aesthetic, lifestyle-oriented. Composition: Airy and spacious with generous negative space. "
    "Characters: Stylized cute figures with soft rounded features, modern casual fashion. "
    "Mood: Warm, inviting, aspirational but approachable. "
    "Details: Subtle botanical elements, soft bokeh-like backgrounds, delicate floral accents. "
    "Overall: The kind of illustration that gets 100k likes on Instagram."
)

def gen_insta(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 필요합니다.")
    ensure_dir(out_dir)
    def _log(msg):
        if log_fn: log_fn(msg)
    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    safety_settings = {
        genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    }
    _client = genai.Client(api_key=api_key)
    saved = []
    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set(): break
        _log(f"[장면 {i}/{len(scenes)}] 인스타 감성 생성 중...")
        try:
            prompt_parts = []
            if reference_image_path and os.path.exists(reference_image_path):
                prompt_parts.append(Image.open(reference_image_path))
                style_instr = "MIMIC THE ARTISTIC STYLE of the reference image."
            else:
                style_instr = f"STYLE: {_STYLE_gen_insta}"
            final_prompt = f"""Create a single high-quality 16:9 Instagram-worthy illustration.

[Content Rules]
- Visualize the SCENE DESCRIPTION as a soft aesthetic lifestyle illustration.
- Include 1-2 short Korean text elements as decorative typography if it enhances the aesthetic.
- Focus on mood and atmosphere over literal depiction.

SCENE DESCRIPTION: {scene_text}

[Style Rules]
{style_instr}
- Warm dreamy pastel tones. Minimal clean composition.
- Perfect for Instagram / Pinterest aesthetic feed.
""".strip()
            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )
            img_data = None
            for part in (response.parts or []):
                if getattr(part, "inline_data", None):
                    img_data = part.inline_data.data; break
            if not img_data:
                _log(f"❌ 실패 (장면 {i}): 이미지 없음"); continue
            final_img = Image.open(io.BytesIO(img_data))
            fname = f"insta_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, fname)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장: {save_path}")
        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue
        time.sleep(0.3)
    return saved


# ==================================================
# 📊 인포그래픽
# ==================================================

_STYLE_gen_infographic = (
    "Art Style: **Clean professional infographic / card news style**. Key Visuals: Bold typography hierarchy, "
    "flat design icons, data visualization elements (charts, graphs, progress bars, numbered lists). "
    "Color: Strong brand colors with high contrast — typically 2-3 dominant colors with white space. "
    "Layout: Grid-based structured layout. Information hierarchy is crystal clear. "
    "Typography: Bold Korean headline, clean body text, numbered or bulleted key points. "
    "Mood: Professional, trustworthy, educational. Like a premium Korean card news or infographic post. "
    "IMPORTANT: ALL TEXT MUST BE IN KOREAN. Include actual readable Korean text in the design."
)

def gen_infographic(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 필요합니다.")
    ensure_dir(out_dir)
    def _log(msg):
        if log_fn: log_fn(msg)
    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    _client = genai.Client(api_key=api_key)
    saved = []
    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set(): break
        _log(f"[장면 {i}/{len(scenes)}] 인포그래픽 생성 중...")
        try:
            prompt_parts = []
            if reference_image_path and os.path.exists(reference_image_path):
                prompt_parts.append(Image.open(reference_image_path))
                style_instr = "MIMIC THE DESIGN STYLE of the reference image."
            else:
                style_instr = f"STYLE: {_STYLE_gen_infographic}"
            final_prompt = f"""Create a single high-quality 16:9 infographic card.

[Content Rules]
- Extract the KEY FACTS and DATA POINTS from the SCENE DESCRIPTION.
- Organize them into a clear visual hierarchy: 1 bold headline + 3-5 key points.
- Use numbers, percentages, or statistics if mentioned in the scene.
- ALL TEXT MUST BE IN KOREAN (한국어).
- Make the text actually readable and informative.

SCENE DESCRIPTION: {scene_text}

[Style Rules]
{style_instr}
- Clean flat design. Strong color blocks. Professional layout.
- Bold Korean typography. Data visualization elements where appropriate.
""".strip()
            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )
            img_data = None
            for part in (response.parts or []):
                if getattr(part, "inline_data", None):
                    img_data = part.inline_data.data; break
            if not img_data:
                _log(f"❌ 실패 (장면 {i}): 이미지 없음"); continue
            final_img = Image.open(io.BytesIO(img_data))
            fname = f"infographic_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, fname)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장: {save_path}")
        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue
        time.sleep(0.3)
    return saved


# ==================================================
# 🎞️ 레트로
# ==================================================

_STYLE_gen_retro = (
    "Art Style: **Retro / Vintage aesthetic**. Key Visuals: Grainy film texture, faded color palette "
    "(muted yellows, warm oranges, dusty blues, sepia tones), halftone dot patterns. "
    "Typography: Vintage serif fonts, distressed lettering, retro badge/stamp designs. "
    "Influences: 1970s-80s Korean commercial art, vintage travel posters, old magazine ads, "
    "VHS aesthetics, Polaroid photo style. "
    "Details: Film grain overlay, light leaks, scratches, aged paper texture. "
    "Mood: Nostalgic, warm, timeless. Like a beautifully preserved artifact from the past."
)

def gen_retro(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 필요합니다.")
    ensure_dir(out_dir)
    def _log(msg):
        if log_fn: log_fn(msg)
    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    _client = genai.Client(api_key=api_key)
    saved = []
    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set(): break
        _log(f"[장면 {i}/{len(scenes)}] 레트로 생성 중...")
        try:
            prompt_parts = []
            if reference_image_path and os.path.exists(reference_image_path):
                prompt_parts.append(Image.open(reference_image_path))
                style_instr = "MIMIC THE RETRO STYLE of the reference image."
            else:
                style_instr = f"STYLE: {_STYLE_gen_retro}"
            final_prompt = f"""Create a single high-quality 16:9 retro/vintage style illustration.

[Content Rules]
- Visualize the SCENE DESCRIPTION through a nostalgic retro lens.
- If text is needed, use vintage Korean typography (레트로 감성 한글).
- Embrace aged, worn, nostalgic aesthetic.

SCENE DESCRIPTION: {scene_text}

[Style Rules]
{style_instr}
- Film grain, muted vintage colors, halftone patterns.
- 1970s-80s Korean retro commercial art aesthetic.
""".strip()
            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )
            img_data = None
            for part in (response.parts or []):
                if getattr(part, "inline_data", None):
                    img_data = part.inline_data.data; break
            if not img_data:
                _log(f"❌ 실패 (장면 {i}): 이미지 없음"); continue
            final_img = Image.open(io.BytesIO(img_data))
            fname = f"retro_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, fname)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장: {save_path}")
        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue
        time.sleep(0.3)
    return saved


# ==================================================
# 📖 웹툰
# ==================================================

_STYLE_gen_webtoon = (
    "Art Style: **Premium Korean Webtoon style**. Key Visuals: Clean precise digital line art, "
    "vibrant saturated colors, dynamic panel composition. "
    "Characters: Well-proportioned Korean manhwa characters — expressive eyes, detailed hair, "
    "modern Korean fashion. Emotional expressions are exaggerated for drama. "
    "Backgrounds: Detailed urban Korean settings (cafes, streets, offices) or stylized abstract backgrounds. "
    "Lighting: Dramatic rim lighting, soft cel-shading, occasional sparkle/glow effects. "
    "Mood: Cinematic, emotionally resonant. Like a top-tier Naver or Kakao webtoon panel. "
    "IMPORTANT: If text bubbles are needed, ALL TEXT MUST BE IN KOREAN."
)

def gen_webtoon(
    api_key: str,
    scenes: List[str],
    out_dir: str,
    reference_image_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    log_fn=None,
) -> List[str]:
    if not HAS_GENAI:
        raise Exception("google-generativeai가 필요합니다.")
    ensure_dir(out_dir)
    def _log(msg):
        if log_fn: log_fn(msg)
    api_key = re.sub(r"\s+", "", (api_key or "").strip())
    _client = genai.Client(api_key=api_key)
    saved = []
    for i, scene_text in enumerate(scenes, start=1):
        if cancel_event and cancel_event.is_set(): break
        _log(f"[장면 {i}/{len(scenes)}] 웹툰 생성 중...")
        try:
            prompt_parts = []
            if reference_image_path and os.path.exists(reference_image_path):
                prompt_parts.append(Image.open(reference_image_path))
                style_instr = "MIMIC THE WEBTOON ART STYLE of the reference image. Maintain character consistency."
            else:
                style_instr = f"STYLE: {_STYLE_gen_webtoon}"
            final_prompt = f"""Create a single high-quality Korean webtoon panel (16:9).

[Content Rules]
- Read the SCENE DESCRIPTION carefully and illustrate the exact scene, characters, and emotions described.
- Show character expressions and body language that convey the mood.
- If dialogue is helpful, add 1-2 short Korean speech bubbles (말풍선).
- Focus on storytelling through visual composition.

SCENE DESCRIPTION: {scene_text}

[Style Rules]
{style_instr}
- Clean digital line art, vibrant colors, dramatic cel-shading.
- Premium Naver/Kakao webtoon quality. Cinematic composition.
- ALL TEXT IN KOREAN.
""".strip()
            prompt_parts.append(f"Draw this image: {final_prompt}")
            response = _client.models.generate_content(
                model="models/gemini-3.1-flash-image-preview",
                contents=prompt_parts,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE","TEXT"])
            )
            img_data = None
            for part in (response.parts or []):
                if getattr(part, "inline_data", None):
                    img_data = part.inline_data.data; break
            if not img_data:
                _log(f"❌ 실패 (장면 {i}): 이미지 없음"); continue
            final_img = Image.open(io.BytesIO(img_data))
            fname = f"webtoon_{i:03d}_{now_ts()}.png"
            save_path = os.path.join(out_dir, fname)
            final_img.save(save_path)
            saved.append(save_path)
            _log(f"  └ ✅ 저장: {save_path}")
        except Exception as e:
            import traceback as _tb
            _log(f"❌ 에러 (장면 {i}): {type(e).__name__}: {e}")
            _log(f"   {_tb.format_exc().strip().splitlines()[-1]}")
            continue
        time.sleep(0.3)
    return saved

# ── 스타일 → 함수 매핑 ─────────────────────────────────────
STYLE_FUNC_MAP = {
    "📈 크립토툰":    gen_cryptotoon,
    "📰 한국어 뉴스": gen_news,
    "🎭 팝아트":      gen_popart,
    "🎮 마인크래프트":gen_minecraft,
    "🦠 미니 세포":   gen_cells,
    "💥 시바 짤방":   gen_meme,
    "🎨 수채화 명화": gen_watercolor,
    "🧸 코인가이드":  gen_coinguide,
    "🖼️ 칠판 강의":   gen_chalkboard,
    "🍫 찰흙 클레이": gen_clay,
    "📱 숏츠 세로":   gen_shorts,
    "📸 인스타 감성":  gen_insta,
    "📊 인포그래픽":  gen_infographic,
    "🎞️ 레트로":      gen_retro,
    "📖 웹툰":         gen_webtoon,
}

def run_style_generate(gemini_key, scenes, out_dir, style_key, ref_image_path, cancel_event, log_fn, full_script=""):
    """화풍에 맞는 원본 함수를 시그니처에 맞게 호출"""
    ref_list = [ref_image_path] if ref_image_path else []

    if style_key == "📰 한국어 뉴스":
        return gen_news(
            api_key=gemini_key, full_script=full_script, scenes=scenes,
            out_dir=out_dir, style_prompt="", selected_style_name="한국어 인터넷 뉴스",
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🎭 팝아트":
        return gen_popart(
            api_key=gemini_key, full_script=full_script, scenes=scenes,
            out_dir=out_dir, style_prompt="", selected_style_name="팝아트",
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🎮 마인크래프트":
        return gen_minecraft(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_paths=ref_list, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🎨 수채화 명화":
        return gen_watercolor(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_paths=ref_list, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🧸 코인가이드":
        return gen_coinguide(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_paths=ref_list, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "📱 숏츠 세로":
        return gen_shorts(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            is_shorts=True, reference_image_path=ref_image_path,
            cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "📈 크립토툰":
        return gen_cryptotoon(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🦠 미니 세포":
        return gen_cells(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "💥 시바 짤방":
        return gen_meme(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🖼️ 칠판 강의":
        return gen_chalkboard(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🍫 찰흙 클레이":
        return gen_clay(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "📸 인스타 감성":
        return gen_insta(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "📊 인포그래픽":
        return gen_infographic(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "🎞️ 레트로":
        return gen_retro(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    elif style_key == "📖 웹툰":
        return gen_webtoon(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )
    else:
        # fallback
        return gen_cryptotoon(
            api_key=gemini_key, scenes=scenes, out_dir=out_dir,
            reference_image_path=ref_image_path, cancel_event=cancel_event, log_fn=log_fn,
        )


# ── Grok 영상 변환 ─────────────────────────────────────
def grok_image_to_video(k, image_paths, out_dir, motion_prompt, log_fn=None):
    ensure_dir(out_dir)
    def _log(m):
        if log_fn: log_fn(m)
        bcast(f"VID:{m}")

    saved = []
    for i, img_path in enumerate(image_paths, 1):
        if _cancel.is_set(): break
        _log(f"[{i}/{len(image_paths)}] Grok 영상 변환 중: {os.path.basename(img_path)}")
        try:
            with open(img_path,"rb") as f:
                enc = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(img_path)[1].lower()
            mime = "image/jpeg" if ext in (".jpg",".jpeg") else "image/png"
            data_uri = f"data:{mime};base64,{enc}"
            payload = {"model":"grok-imagine-video","prompt":f"Animate the image. Smooth cinematic motion: {motion_prompt}. STRICTLY SILENT VIDEO, NO AUDIO.","image":{"url":data_uri}}
            hdrs = {"Authorization":f"Bearer {k}","Content-Type":"application/json"}
            res = req.post("https://api.x.ai/v1/videos/generations",headers=hdrs,json=payload,timeout=120)
            if res.status_code>=400: _log(f"❌ Grok API 에러: {res.text}"); continue
            request_id = res.json().get("request_id")
            if not request_id: _log("❌ request_id 없음"); continue
            _log(f"  ⏳ 폴링 중... ID: {request_id[:8]}")
            video_url = None
            for _ in range(60):
                if _cancel.is_set(): break
                time.sleep(5)
                stat = req.get(f"https://api.x.ai/v1/videos/{request_id}",headers=hdrs,timeout=20)
                if stat.status_code>=400: continue
                st = stat.json().get("status")
                if st=="done": video_url=stat.json().get("video",{}).get("url"); break
                if st in ("expired","failed","error"): _log(f"❌ 렌더링 실패: {st}"); break
            if video_url:
                vid_res = req.get(video_url,stream=True,timeout=60)
                if vid_res.status_code==200:
                    vfname = f"grok_video_{i:03d}_{now_ts()}.mp4"
                    vpath = os.path.join(out_dir,vfname)
                    with open(vpath,"wb") as f:
                        for chunk in vid_res.iter_content(8192): f.write(chunk)
                    saved.append(vpath); _log(f"✅ 영상 저장: {vfname}")
        except Exception as e:
            _log(f"❌ 구간 {i} 에러: {e}"); continue
    return saved

# ── Flask ──────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200*1024*1024

# ─── 공통 API ───────────────────────────────────────────
@app.route("/")
def index(): return HTML_PAGE

@app.route("/api/keys", methods=["GET","POST"])
def api_keys():
    if request.method=="GET":
        return jsonify({s: bool(read_key(s)) for s in KEY_FILES})
    data = request.json or {}
    for s,v in data.items():
        if v and s in KEY_FILES: save_key(s,v)
    return jsonify({"ok":True})

@app.route("/api/logs")
def api_logs():
    import queue as qmod
    tab_id = request.args.get("tab_id", "default")
    q = qmod.Queue(maxsize=300)
    with _log_lock:
        _log_qs[tab_id] = q
    def gen():
        try:
            while True:
                try: msg=q.get(timeout=30); yield f"data: {json.dumps({'msg':msg})}\n\n"
                except: yield ": keep-alive\n\n"
        except GeneratorExit:
            with _log_lock:
                if _log_qs.get(tab_id) is q:
                    _log_qs.pop(tab_id, None)
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    tab_id = (request.json or {}).get("tab_id", None)
    if tab_id:
        ev = _tab_cancel.get(tab_id)
        if ev: ev.set()
        bcast("CANCEL:⛔ 중단 요청됨", tab_id)
    else:
        _cancel.set()
        bcast("CANCEL:⛔ 중단 요청됨")
    return jsonify({"ok":True})

@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    path = (request.json or {}).get("path", DEFAULT_OUT)
    ensure_dir(path)
    try:
        if sys.platform=="win32": os.startfile(path)
        elif sys.platform=="darwin": subprocess.Popen(["open",path])
        else: subprocess.Popen(["xdg-open",path])
    except: pass
    return jsonify({"ok":True})

@app.route("/api/folder-dialog", methods=["POST"])
def api_folder_dialog():
    init_dir = (request.json or {}).get("init_dir", DEFAULT_OUT)
    try:
        if sys.platform=="win32":
            cmd=["powershell","-NoProfile","-Command",
                 "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
                 "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
                 f"$f.SelectedPath = '{init_dir}';"
                 "$f.Description = '저장 폴더 선택';"
                 "$null = $f.ShowDialog();"
                 "Write-Output $f.SelectedPath"]
            result=subprocess.check_output(cmd,timeout=60,text=True,creationflags=0x08000000).strip()
            if result and os.path.isdir(result): return jsonify({"path":result})
    except: pass
    return jsonify({"path":None})

# ─── TTS API ───────────────────────────────────────────
@app.route("/api/tts/recent-voices", methods=["GET","POST"])
def recent_voices():
    path = os.path.join(SCRIPT_DIR, "recent_voices.json")
    if request.method == "GET":
        try:
            if os.path.exists(path):
                with open(path,"r",encoding="utf-8") as f_: return jsonify(json.load(f_))
        except: pass
        return jsonify({"voices":[]})
    # POST: 최근 사용 목소리 저장 (최대 5개)
    data = request.json or {}
    voice = data.get("voice")
    if not voice: return jsonify({"ok":False}),400
    try:
        existing = []
        if os.path.exists(path):
            with open(path,"r",encoding="utf-8") as f_: existing = json.load(f_).get("voices",[])
        # 중복 제거 후 맨 앞에 추가
        existing = [v for v in existing if v.get("voice_id") != voice.get("voice_id")]
        existing.insert(0, voice)
        existing = existing[:5]
        with open(path,"w",encoding="utf-8") as f_: json.dump({"voices":existing},f_,ensure_ascii=False)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/tts/voices")
def tts_voices():
    k=read_key("elevenlabs")
    if not k: return jsonify({"error":"ElevenLabs API 키 없음. 설정 탭에서 키를 입력하세요."}),401
    try:
        raw=el_voices(k)
        out=[]
        for v in raw:
            try:
                lb=v.get("labels",{}) or {}
                out.append({"voice_id":v["voice_id"],"name":v.get("name",""),"category":v.get("category","generated"),
                            "gender":lb.get("gender",""),"age":lb.get("age",""),"accent":lb.get("accent",""),
                            "use_case":lb.get("use_case",""),"preview_url":v.get("preview_url","")})
            except Exception:
                continue
        return jsonify({"voices":out})
    except req.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else 500
        if code==401: return jsonify({"error":"API 키가 유효하지 않습니다"}),401
        return jsonify({"error":f"ElevenLabs 오류 {code}: {e}"}),500
    except Exception as e:
        import traceback as _tb
        print("[voices ERROR]", _tb.format_exc())
        return jsonify({"error":str(e)}),500

@app.route("/api/tts/models")
def tts_models():
    # 항상 우리가 정의한 메타(지원 파라미터 포함)를 반환
    # API 호출로 가져오는 모델 목록은 이름만 다르고 메타가 없으므로 로컬 정의 우선
    return jsonify({"models": EL_MODELS})

@app.route("/api/tts/user")
def tts_user():
    k=read_key("elevenlabs")
    if not k: return jsonify({"error":"키 없음"}),401
    try:
        info=el_user(k)
        sub=info.get("subscription") or {}
        def _i(v):
            try: return int(v or 0)
            except: return 0
        return jsonify({"tier":str(sub.get("tier") or sub.get("plan") or "Free"),
                        "char_used":_i(sub.get("character_count") or sub.get("characters_used") or 0),
                        "char_limit":_i(sub.get("character_limit") or sub.get("characters_limit") or 0)})
    except req.exceptions.HTTPError as e:
        if e.response and e.response.status_code==401: return jsonify({"error":"키 무효"}),401
        return jsonify({"error":str(e)}),500
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/tts/generate", methods=["POST"])
def tts_generate():
    k=read_key("elevenlabs")
    if not k: return jsonify({"error":"ElevenLabs 키 없음"}),401
    d=request.json or {}
    script=d.get("script","").strip()
    voice_id=d.get("voice_id","").strip()
    if not script: return jsonify({"error":"대본 없음"}),400
    if not voice_id: return jsonify({"error":"목소리 선택 필요"}),400

    split_mode=bool(d.get("split_mode",True))
    scenes=parse_scenes(script) if split_mode else [script]
    if not scenes: scenes=[script]

    enhance=bool(d.get("enhance",False))
    model_id=d.get("model_id","eleven_multilingual_v2")
    stability=float(d.get("stability",0.5))
    similarity=float(d.get("similarity_boost",0.75))
    style=float(d.get("style",0.0))
    spk_boost=bool(d.get("use_speaker_boost",True))
    speed=float(d.get("speed",1.0))
    fmt=d.get("output_format","mp3_44100_128")
    prefix=(d.get("prefix","scene") or "scene").strip()
    base_dir=(d.get("out_dir","") or DEFAULT_OUT).strip()
    project=re.sub(r'[\\/:*?"<>|]',"_",(d.get("project","") or "").strip())

    ts=now_ts()
    folder=f"{project}_{ts}" if project else f"tts_{ts}"
    out_dir=os.path.join(base_dir,folder)
    out_dir=ensure_dir(out_dir) or out_dir
    ext=ext_for_fmt(fmt)
    _cancel.clear()

    _tid = d.get("tab_id","default")
    cancel_ev = threading.Event()
    _tab_cancel[_tid] = cancel_ev
    def _b(m): bcast(m, _tid)
    def worker():
        total=len(scenes)
        _b(f"INFO:🎙️ TTS 시작 — {total}개 구간")
        _b(f"INFO:📁 {out_dir}")
        saved=[]
        for i,text in enumerate(scenes,1):
            if cancel_ev.is_set() or _cancel.is_set(): _b(f"CANCEL:⛔ 중단 ({i-1}/{total} 완료)"); return
            if not text.strip(): _b(f"SKIP:구간 {i} 비어있음"); continue
            _b(f"PROG:{i}/{total}|TTS 구간 {i} 생성 중 ({len(text)}자){'  [Enhance ON]' if enhance else ''}")
            try:
                prev_ctx = scenes[i-2] if enhance and i > 1     else None
                next_ctx = scenes[i]   if enhance and i < total else None
                audio=el_tts(k,voice_id,text,model_id,stability,similarity,style,spk_boost,speed,fmt,
                             prev_text=prev_ctx, next_text=next_ctx, enhance=enhance,
                             gemini_key=read_key("gemini"), full_script=script)
                if cancel_ev.is_set() or _cancel.is_set(): _b("CANCEL:⛔ 중단"); return
                fname=f"{prefix}_{i:03d}.{ext}" if split_mode else f"{prefix}_full.{ext}"
                fpath=os.path.join(out_dir,fname)
                with open(fpath+".tmp","wb") as f_: f_.write(audio)
                os.replace(fpath+".tmp",fpath)
                saved.append(fpath)
                _b(f"OK:구간 {i} → {folder}/{fname} ({len(audio):,}B)")
            except Exception as e:
                detail = ""
                if hasattr(e, "response") and e.response is not None:
                    try: detail = " — " + str(e.response.json())
                    except: detail = " — " + e.response.text[:200]
                _b(f"ERR:구간 {i} 실패: {e}{detail}")
            if i<total: time.sleep(0.1)
        _b(f"DONE:{len(saved)}|{out_dir}")

    threading.Thread(target=worker,daemon=True).start()
    return jsonify({"ok":True,"scenes":len(scenes),"out_dir":out_dir})

@app.route("/api/tts/preview", methods=["POST"])
def tts_preview():
    k=read_key("elevenlabs")
    if not k: return jsonify({"error":"키 없음"}),401
    d=request.json or {}
    text=(d.get("text","") or "").strip()
    if not text: return jsonify({"error":"텍스트 없음"}),400
    try:
        audio=el_tts(k,d.get("voice_id",""),text,d.get("model_id","eleven_multilingual_v2"),
                     float(d.get("stability",0.5)),float(d.get("similarity_boost",0.75)),
                     float(d.get("style",0.0)),bool(d.get("use_speaker_boost",True)),
                     float(d.get("speed",1.0)),d.get("output_format","mp3_44100_128"))
        fmt=d.get("output_format","mp3_44100_128")
        mt="audio/mpeg" if fmt.startswith("mp3") else "audio/wav"
        return Response(audio,mimetype=mt,headers={"Content-Disposition":"inline; filename=preview.mp3"})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/tts/history")
def tts_history():
    k=read_key("elevenlabs")
    if not k: return jsonify({"error":"키 없음"}),401
    try:
        hist=el_history(k,20)
        return jsonify({"history":[{"voice_name":h.get("voice_name",""),"text":(h.get("text") or "")[:80],"date":h.get("date_unix",""),"chars":h.get("character_count_change_from",0)} for h in hist]})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/tts/clone", methods=["POST"])
def tts_clone():
    k=read_key("elevenlabs")
    if not k: return jsonify({"error":"키 없음"}),401
    name=(request.form.get("name") or "").strip()
    desc=(request.form.get("description") or "").strip()
    if not name: return jsonify({"error":"이름 없음"}),400
    files_up=request.files.getlist("files")
    if not files_up: return jsonify({"error":"파일 없음"}),400
    tmp=os.path.join(DEFAULT_OUT,"_clone_tmp"); ensure_dir(tmp)
    saved=[]
    for f in files_up: p=os.path.join(tmp,f.filename); f.save(p); saved.append(p)
    try:
        res=el_clone(k,name,saved,desc)
        return jsonify({"ok":True,"voice_id":res.get("voice_id","?")})
    except Exception as e: return jsonify({"error":str(e)}),500
    finally:
        for p in saved:
            try: os.remove(p)
            except: pass

# ─── 이미지 생성 API ────────────────────────────────────
@app.route("/api/img/styles")
def img_styles():
    # 숏츠만 9:16, 나머지 16:9
    return jsonify({"styles":[
        {"key":k, "ratio":"9:16" if "숏츠" in k else "16:9", "custom": False}
        for k in COMIC_STYLES.keys()
    ]})

@app.route("/api/img/generate", methods=["POST"])
def img_generate():
    k=read_key("gemini")
    if not k: return jsonify({"error":"Gemini API 키 없음"}),401
    d=request.json or {}
    script=d.get("script","").strip()
    if not script: return jsonify({"error":"대본 없음"}),400
    scenes=parse_scenes(script)
    if not scenes: scenes=[script]

    style_key=d.get("style_key","🖼️ 칠판 강의 (Chalkboard)")
    custom_prompt=d.get("custom_prompt","")
    base_dir=(d.get("out_dir","") or DEFAULT_OUT).strip()
    project=re.sub(r'[\\/:*?"<>|]',"_",(d.get("project","") or "").strip())
    ratio="9:16" if "숏츠" in style_key else "16:9"

    ts=now_ts(); folder=f"{project}_{ts}" if project else f"img_{ts}"
    out_dir=os.path.join(base_dir,folder)
    out_dir=ensure_dir(out_dir) or out_dir

    ref_path=(d.get("ref_image_path") or None)
    _cancel.clear()

    _tid = d.get("tab_id","default")
    cancel_ev2 = threading.Event()
    _tab_cancel[_tid] = cancel_ev2
    def _b(m): bcast(m, _tid)
    def worker():
        _b(f"INFO:🎨 이미지 생성 시작 — {len(scenes)}개 구간")
        _b(f"INFO:🖌️ 화풍: {style_key}")
        imgs = run_style_generate(k, scenes, out_dir, style_key, ref_path, _cancel, lambda m: _b(f"INFO:{m}"), full_script=script)
        _b(f"DONE:{len(imgs)}|{out_dir}")

    threading.Thread(target=worker,daemon=True).start()
    return jsonify({"ok":True,"scenes":len(scenes)})

@app.route("/api/img/upload-ref", methods=["POST"])
def img_upload_ref():
    f=request.files.get("file")
    if not f: return jsonify({"error":"파일 없음"}),400
    tmp=os.path.join(DEFAULT_OUT,"_ref_tmp"); ensure_dir(tmp)
    path=os.path.join(tmp,"ref_"+f.filename)
    f.save(path)
    return jsonify({"ok":True,"path":path})

# ─── 콤보 생성 API (이미지 + TTS 동시) ────────────────────
@app.route("/api/combo/generate", methods=["POST"])
def combo_generate():
    gk=read_key("gemini"); ek=read_key("elevenlabs")
    if not gk: return jsonify({"error":"Gemini API 키 없음"}),401
    if not ek: return jsonify({"error":"ElevenLabs API 키 없음"}),401
    d=request.json or {}
    script=d.get("script","").strip()
    voice_id=d.get("voice_id","").strip()
    if not script: return jsonify({"error":"대본 없음"}),400
    if not voice_id: return jsonify({"error":"목소리 선택 필요"}),400

    scenes=parse_scenes(script)
    if not scenes: scenes=[script]

    style_key=d.get("style_key","📈 크립토툰 만화 (CryptoToon)")
    custom_prompt=d.get("custom_prompt","")
    ref_path=(d.get("ref_image_path") or None)
    ratio="9:16" if "숏츠" in style_key else "16:9"

    model_id=d.get("model_id","eleven_multilingual_v2")
    stability=float(d.get("stability",0.5)); similarity=float(d.get("similarity_boost",0.75))
    style_tts=float(d.get("style",0.0)); spk_boost=bool(d.get("use_speaker_boost",True))
    speed=float(d.get("speed",1.0)); fmt=d.get("output_format","mp3_44100_128")
    enhance=bool(d.get("enhance",False))
    prefix=(d.get("prefix","scene") or "scene").strip()
    base_dir=(d.get("out_dir","") or DEFAULT_OUT).strip()
    project=re.sub(r'[\\/:*?"<>|]',"_",(d.get("project","") or "").strip())

    ts=now_ts(); folder=f"{project}_{ts}" if project else f"combo_{ts}"
    out_dir=os.path.join(base_dir,folder)
    out_dir=ensure_dir(out_dir) or out_dir
    img_dir=os.path.join(out_dir,"images")
    tts_dir=os.path.join(out_dir,"audio")
    img_dir=ensure_dir(img_dir) or img_dir
    tts_dir=ensure_dir(tts_dir) or tts_dir
    ext=ext_for_fmt(fmt)
    _cancel.clear()

    _tid = d.get("tab_id","default")
    cancel_ev3 = threading.Event()
    _tab_cancel[_tid] = cancel_ev3
    def _b(m): bcast(m, _tid)
    def worker():
        total=len(scenes)
        _b(f"INFO:🎬 콤보 생성 시작 — {total}개 구간 (이미지 + 음성)")
        _b(f"INFO:📁 저장 폴더: {out_dir}")

        img_saved=[]; tts_saved=[]
        import concurrent.futures as _cf

        def _gen_image(i, scene_text):
            try:
                imgs = run_style_generate(gk, [scene_text], img_dir, style_key, ref_path, _cancel, lambda m: _b(f"INFO:{m}"), full_script=script)
                if imgs:
                    _b(f"OK:구간 {i} 이미지 → {os.path.basename(imgs[0])}")
                    return imgs[0]
                else:
                    _b(f"ERR:구간 {i} 이미지 생성 실패"); return None
            except Exception as e:
                _b(f"ERR:구간 {i} 이미지 오류: {e}"); return None

        def _gen_tts(i, scene_text):
            try:
                prev_ctx = scenes[i-2] if enhance and i>1     else None
                next_ctx = scenes[i]   if enhance and i<total else None
                audio=el_tts(ek,voice_id,scene_text,model_id,stability,similarity,style_tts,spk_boost,speed,fmt,
                             prev_text=prev_ctx, next_text=next_ctx, enhance=enhance,
                             gemini_key=read_key("gemini"), full_script=script)
                fname=f"{prefix}_{i:03d}.{ext}"
                fpath=os.path.join(tts_dir,fname)
                with open(fpath+".tmp","wb") as f_: f_.write(audio)
                os.replace(fpath+".tmp",fpath)
                _b(f"OK:구간 {i} 음성 → {fname} ({len(audio):,}B)")
                return fpath
            except Exception as e:
                detail = ""
                if hasattr(e, "response") and e.response is not None:
                    try: detail = " — " + str(e.response.json())
                    except: detail = " — " + e.response.text[:200]
                _b(f"ERR:구간 {i} 음성 오류: {e}{detail}"); return None

        # 이미지와 음성을 구간마다 동시에 처리
        with _cf.ThreadPoolExecutor(max_workers=2) as executor:
            for i, scene_text in enumerate(scenes, 1):
                if cancel_ev3.is_set() or _cancel.is_set():
                    _b(f"CANCEL:⛔ 중단됨 ({i-1}/{total} 완료)"); return
                if not scene_text.strip():
                    _b(f"SKIP:구간 {i} 비어있음"); continue

                _b(f"PROG:{i}/{total}|구간 {i} — 이미지 + 음성 동시 생성 중...")
                # 이미지와 TTS 동시 실행
                f_img = executor.submit(_gen_image, i, scene_text)
                f_tts = executor.submit(_gen_tts,   i, scene_text)
                img_result = f_img.result()
                tts_result = f_tts.result()
                if img_result: img_saved.append(img_result)
                if tts_result: tts_saved.append(tts_result)

        _b(f"INFO:✅ 이미지 {len(img_saved)}개 / 음성 {len(tts_saved)}개 생성 완료")
        _b(f"DONE:{len(img_saved)+len(tts_saved)}|{out_dir}")

    threading.Thread(target=worker,daemon=True).start()
    return jsonify({"ok":True,"scenes":len(scenes),"out_dir":out_dir})

# ─── HTML ──────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>황작가 AI 스튜디오</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg0:#0f0f0f;--bg1:#161616;--bg2:#1e1e1e;--bg3:#252525;--bg4:#2e2e2e;
  --bd:#2f2f2f;--bd2:#3a3a3a;
  --tx0:#f2f2f2;--tx1:#c8c8c8;--tx2:#888;--tx3:#555;
  --acc:#f5a623;--acc2:#e8951c;--acc-dim:rgba(245,166,35,0.12);
  --red:#e05252;--green:#4caf78;--blue:#5b9cf6;
  --r4:4px;--r6:6px;--r8:8px;--r12:12px;
  --font:'Inter',system-ui,sans-serif;--sb:220px;
}
html,body{height:100%;overflow:hidden;font-family:var(--font);background:var(--bg0);color:var(--tx0);font-size:13px}
.layout{display:flex;height:100vh}
.sidebar{width:var(--sb);min-width:var(--sb);background:var(--bg1);border-right:1px solid var(--bd);display:flex;flex-direction:column;flex-shrink:0}
.sb-logo{padding:18px 14px 14px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:10px}
.sb-logo-icon{width:28px;height:28px;background:var(--acc);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.sb-logo-text{font-size:14px;font-weight:700;color:var(--tx0)}
.sb-logo-sub{font-size:9px;color:var(--tx3);margin-top:1px}
.sb-nav{flex:1;padding:8px;overflow-y:auto}
.nav-group{margin-bottom:6px}
.nav-group-label{font-size:10px;font-weight:600;color:var(--tx3);text-transform:uppercase;letter-spacing:.8px;padding:8px 8px 4px}
.nav-btn{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:var(--r6);cursor:pointer;color:var(--tx1);font-size:13px;border:none;background:none;width:100%;text-align:left;transition:all .15s}
.nav-btn:hover{background:var(--bg3);color:var(--tx0)}
.nav-btn.active{background:var(--acc-dim);color:var(--acc)}
.nav-btn .icon{font-size:15px;width:18px;text-align:center;flex-shrink:0}
.sb-bottom{padding:10px 8px;border-top:1px solid var(--bd)}
.key-row{display:flex;align-items:center;gap:6px;padding:3px 6px}
.key-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.key-dot.ok{background:var(--green)}.key-dot.no{background:var(--tx3)}
.key-name{font-size:11px;color:var(--tx2)}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.topbar{height:50px;min-height:50px;border-bottom:1px solid var(--bd);background:var(--bg1);display:flex;align-items:center;padding:0 20px;gap:12px;flex-shrink:0}
.topbar-title{font-size:15px;font-weight:600}
.topbar-sub{font-size:12px;color:var(--tx2)}
.content{flex:1;overflow:hidden;display:flex}
.page{display:none;width:100%;height:100%}
.page.active{display:flex}
.split{display:flex;width:100%;height:100%}
.split-left{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--bd);overflow:hidden;min-width:0}
.split-right{width:330px;min-width:330px;display:flex;flex-direction:column;background:var(--bg1);overflow:hidden}
.split-right.w360{width:360px;min-width:360px}
.script-hdr{padding:12px 16px 8px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:8px;flex-shrink:0}
.script-hdr h3{font-size:12px;font-weight:600;flex:1}
.badge{background:var(--acc-dim);color:var(--acc);border-radius:20px;padding:2px 9px;font-size:10px;font-weight:600}
.script-area{flex:1;position:relative;overflow:hidden}
textarea.script{width:100%;height:100%;background:var(--bg0);border:none;outline:none;resize:none;color:var(--tx0);font-family:var(--font);font-size:14px;line-height:1.75;padding:14px 18px;caret-color:var(--acc)}
textarea.script::placeholder{color:var(--tx3)}
.script-foot{padding:8px 16px;border-top:1px solid var(--bd);display:flex;align-items:center;gap:8px;flex-shrink:0}
.panel-scroll{flex:1;overflow-y:auto}
.panel-sec{border-bottom:1px solid var(--bd)}
.panel-hdr{padding:10px 14px 8px;font-size:10px;font-weight:600;color:var(--tx2);text-transform:uppercase;letter-spacing:.6px}
.panel-body{padding:0 14px 12px}
.panel-foot{padding:14px 14px 16px;border-top:1px solid var(--bd);background:var(--bg1);flex-shrink:0;position:relative;z-index:10}
.field-label{font-size:11px;color:var(--tx2);margin-bottom:5px}
.fi{width:100%;background:var(--bg2);border:1px solid var(--bd2);border-radius:var(--r6);padding:7px 10px;color:var(--tx0);font-size:12px;font-family:var(--font);outline:none}
.fi:focus{border-color:var(--acc)}.fi::placeholder{color:var(--tx3)}
select.fi{appearance:none;background-image:url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1L5 5L9 1' stroke='%23555' stroke-width='1.5' stroke-linecap='round' fill='none'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;cursor:pointer}
.field-row{margin-bottom:10px}
.field-row2{display:flex;gap:8px;margin-bottom:10px}
.field-row2 .field-row{flex:1;margin-bottom:0}
.path-row{display:flex;gap:5px}
.path-row .fi{flex:1;cursor:pointer}
.slider-row{margin-bottom:12px}
.slider-lbl{display:flex;justify-content:space-between;margin-bottom:5px}
.slider-lbl span{font-size:11px;color:var(--tx1);font-weight:500}
.slider-lbl .val{font-size:11px;color:var(--acc);font-weight:700;min-width:28px;text-align:right}
input[type=range]{width:100%;-webkit-appearance:none;height:4px;border-radius:2px;background:var(--bg4);outline:none;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--acc);cursor:pointer;box-shadow:0 0 0 3px rgba(245,166,35,.2)}
.slider-hints{display:flex;justify-content:space-between;margin-top:3px}
.slider-hints span{font-size:10px;color:var(--tx3)}
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:5px 0}
.toggle-label{font-size:12px;color:var(--tx1)}
.toggle{position:relative;display:inline-block;width:34px;height:18px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.toggle-sl{position:absolute;inset:0;background:var(--bg4);border-radius:9px;transition:.2s;cursor:pointer}
.toggle-sl:before{content:"";position:absolute;width:12px;height:12px;left:3px;bottom:3px;background:var(--tx2);border-radius:50%;transition:.2s}
.toggle input:checked+.toggle-sl{background:var(--acc)}
.toggle input:checked+.toggle-sl:before{transform:translateX(16px);background:#000}
.model-tabs{display:flex;gap:4px;flex-wrap:wrap}
.mtab{padding:4px 9px;border-radius:var(--r4);border:1px solid var(--bd2);background:transparent;color:var(--tx1);cursor:pointer;font-size:11px;font-family:var(--font);transition:all .15s;white-space:nowrap;position:relative}
.mtab:hover{border-color:var(--acc);color:var(--acc)}
.mtab.active{background:var(--acc);border-color:var(--acc);color:#000;font-weight:600}
.mtab[title]:hover::after{content:attr(title);position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);background:var(--bg4);color:var(--tx0);font-size:10px;padding:4px 8px;border-radius:4px;white-space:nowrap;pointer-events:none;border:1px solid var(--bd2);z-index:100}
.voice-btn{width:100%;background:var(--bg2);border:1px solid var(--bd2);border-radius:var(--r8);padding:9px 12px;display:flex;align-items:center;gap:10px;cursor:pointer;transition:all .15s}
.voice-btn:hover{border-color:var(--acc)}
.voice-btn.selected{border-color:var(--acc);background:var(--acc-dim)}
.vb-av{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;flex-shrink:0}
.vb-info{flex:1;text-align:left}
.vb-name{font-size:12px;font-weight:500;color:var(--tx0)}
.vb-meta{font-size:10px;color:var(--tx2)}
.style-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.style-card{background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r8);padding:9px 10px;cursor:pointer;transition:all .15s}
.style-card:hover{border-color:var(--acc);background:var(--bg3)}
.style-card.active{border-color:var(--acc);background:var(--acc-dim)}
.style-card-name{font-size:11px;font-weight:500;color:var(--tx0)}
.style-card-ratio{font-size:9px;color:var(--tx3);margin-top:2px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border-radius:var(--r6);cursor:pointer;font-family:var(--font);font-size:12px;font-weight:500;transition:all .15s;border:none;padding:6px 12px;white-space:nowrap}
.btn-primary{background:var(--acc);color:#000}.btn-primary:hover{background:var(--acc2)}
.btn-ghost{background:transparent;color:var(--tx1);border:1px solid var(--bd2)}
.btn-ghost:hover{border-color:var(--acc);color:var(--acc);background:var(--acc-dim)}
.btn-danger{background:transparent;color:var(--red);border:1px solid var(--red)}
.btn-danger:hover{background:rgba(224,82,82,.1)}
.btn-icon{padding:5px 8px;border-radius:var(--r4);background:transparent;border:1px solid var(--bd2);color:var(--tx1);cursor:pointer;font-size:13px;font-family:var(--font);transition:all .15s}
.btn-icon:hover{border-color:var(--acc);color:var(--acc)}
.gen-btn{width:100%;padding:14px 10px;font-size:15px;font-weight:700;border-radius:var(--r8);background:var(--acc);color:#000;border:none;cursor:pointer;font-family:var(--font);transition:all .15s;display:flex;align-items:center;justify-content:center;gap:8px;min-height:52px;letter-spacing:.3px;box-shadow:0 2px 8px rgba(245,166,35,.35);position:relative;z-index:10}
.gen-btn:hover{background:var(--acc2);box-shadow:0 4px 16px rgba(245,166,35,.5);transform:translateY(-1px)}.gen-btn:active{transform:translateY(0)}.gen-btn:disabled{opacity:.4;cursor:not-allowed;box-shadow:none}
.prog-wrap{margin-bottom:8px;display:none}
.prog-hdr{display:flex;justify-content:space-between;margin-bottom:4px}
.prog-lbl{font-size:11px;color:var(--tx2)}.prog-pct{font-size:11px;color:var(--acc);font-weight:600}
.prog-bar{height:3px;background:var(--bg4);border-radius:2px;overflow:hidden}
.prog-fill{height:100%;background:var(--acc);border-radius:2px;transition:width .3s;width:0%}
.log-box{background:var(--bg0);border:1px solid var(--bd);border-radius:var(--r6);padding:8px 10px;height:110px;max-height:110px;overflow-y:auto;font-family:'SF Mono','Courier New',monospace;font-size:10px;line-height:1.6;display:none;margin-bottom:8px}
.log-box .l-ok{color:var(--green)}.log-box .l-err{color:var(--red)}.log-box .l-info{color:var(--tx1)}
.log-box .l-skip{color:var(--tx3)}.log-box .l-prog{color:var(--acc)}.log-box .l-cancel{color:var(--red)}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;z-index:1000;display:none;backdrop-filter:blur(4px)}
.modal-bg.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--bd2);border-radius:14px;width:780px;max-width:95vw;height:560px;display:flex;flex-direction:column;overflow:hidden}
.modal-hdr{padding:14px 18px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:10px}
.modal-hdr h3{font-size:14px;font-weight:600;flex:1}
.modal-close{width:26px;height:26px;border-radius:50%;background:var(--bg3);border:none;cursor:pointer;color:var(--tx1);font-size:14px}
.modal-toolbar{padding:10px 14px;border-bottom:1px solid var(--bd);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.modal-grid{flex:1;overflow-y:auto;padding:10px 14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;align-content:start}
.voice-card{background:var(--bg3);border:1px solid var(--bd);border-radius:var(--r8);padding:11px 12px;cursor:pointer;transition:all .15s}
.voice-card:hover{border-color:var(--acc)}.voice-card.sel{border-color:var(--acc);background:var(--acc-dim)}
.vc-top{display:flex;align-items:center;gap:8px;margin-bottom:7px}
.vc-av{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;flex-shrink:0}
.vc-name{font-size:12px;font-weight:600;color:var(--tx0)}.vc-cat{font-size:10px;color:var(--tx3);text-transform:uppercase}
.vc-tags{display:flex;gap:3px;flex-wrap:wrap;margin-bottom:8px}
.vc-tag{background:var(--bg4);color:var(--tx2);border-radius:3px;padding:1px 6px;font-size:10px}
.vc-actions{display:flex;align-items:center;gap:5px}
.play-btn{width:26px;height:26px;border-radius:50%;background:var(--bg4);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--tx1);font-size:10px;transition:all .15s}
.play-btn:hover{background:var(--acc);color:#000}
.use-btn{margin-left:auto;padding:4px 10px;border-radius:3px;background:transparent;border:1px solid var(--bd2);color:var(--tx1);font-size:10px;font-weight:500;cursor:pointer;font-family:var(--font);transition:all .15s}
.use-btn:hover{border-color:var(--acc);color:var(--acc)}
.filter-chips{display:flex;gap:5px;flex-wrap:wrap}
.chip{padding:3px 10px;border-radius:20px;border:1px solid var(--bd2);background:transparent;color:var(--tx1);font-size:11px;font-weight:500;cursor:pointer;font-family:var(--font);transition:all .15s;white-space:nowrap}
.chip:hover{border-color:var(--acc);color:var(--acc)}.chip.active{background:var(--acc);border-color:var(--acc);color:#000}
.search-wrap{position:relative;flex:1;min-width:180px;max-width:280px}
.search-wrap svg{position:absolute;left:9px;top:50%;transform:translateY(-50%);width:12px;height:12px;pointer-events:none;color:var(--tx3)}
.search-in{width:100%;background:var(--bg2);border:1px solid var(--bd2);border-radius:var(--r6);padding:6px 10px 6px 28px;color:var(--tx0);font-size:12px;outline:none;font-family:var(--font)}
.search-in:focus{border-color:var(--acc)}.search-in::placeholder{color:var(--tx3)}
/* ref image drop zone */
.ref-drop{border:2px dashed var(--bd2);border-radius:var(--r8);padding:14px;text-align:center;cursor:pointer;transition:all .15s;position:relative}
.ref-drop:hover,.ref-drop.has-img{border-color:var(--acc);background:var(--acc-dim)}
.ref-drop input[type=file]{display:none}
.ref-thumb{width:100%;height:80px;object-fit:cover;border-radius:var(--r4);margin-top:6px;display:none}
/* combo pair display */
.pair-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
.pair-card{background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r8);padding:8px;font-size:11px;color:var(--tx1)}
.pair-num{font-size:10px;color:var(--acc);font-weight:600;margin-bottom:4px}
.notif{position:fixed;bottom:18px;right:18px;background:var(--bg3);border:1px solid var(--bd2);border-radius:var(--r8);padding:9px 14px;font-size:12px;color:var(--tx0);z-index:2000;transform:translateY(16px);opacity:0;transition:all .25s;max-width:300px}
.notif.show{transform:translateY(0);opacity:1}
.notif.ok{border-color:var(--green);color:var(--green)}.notif.err{border-color:var(--red);color:var(--red)}
::-webkit-scrollbar{width:4px;height:4px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:2px}
</style>
</head>
<body>
<div class="layout">

<aside class="sidebar">
  <div class="sb-logo">
    <div class="sb-logo-icon">🎬</div>
    <div><div class="sb-logo-text">황작가 스튜디오</div><div class="sb-logo-sub">AI Creator Suite v2.0</div></div>
  </div>
  <nav class="sb-nav">
    <div class="nav-group">
      <div class="nav-group-label">생성 도구</div>
      <button class="nav-btn" id="nav-script" onclick="gotoPage('script')"><span class="icon">📝</span>대본 생성</button>
      <button class="nav-btn active" id="nav-tts" onclick="gotoPage('tts')"><span class="icon">🎙️</span>TTS 음성 생성</button>
      <button class="nav-btn" id="nav-img" onclick="gotoPage('img')"><span class="icon">🎨</span>이미지 생성</button>
      <button class="nav-btn" id="nav-combo" onclick="gotoPage('combo')"><span class="icon">🎬</span>이미지 + 음성 동시</button>
      <button class="nav-btn" id="nav-settings" onclick="gotoPage('settings')"><span class="icon">⚙️</span>설정 / API 키</button>
    </div>
  </nav>
  <div class="sb-bottom">
    <div class="key-row"><div class="key-dot no" id="dot-elevenlabs"></div><span class="key-name">ElevenLabs (TTS)</span></div>
    <div class="key-row"><div class="key-dot no" id="dot-gemini"></div><span class="key-name">Gemini (이미지)</span></div>
  </div>
</aside>

<div class="main">
  <div class="topbar">
    <div><div class="topbar-title" id="tb-title">TTS 음성 생성</div><div style="font-size:12px;color:var(--tx2)" id="tb-sub">ElevenLabs API로 대본을 음성으로 변환합니다</div></div>
  </div>
  <div class="content">

  <!-- ═══ 대본 생성 ═══ -->
  <div class="page" id="page-script">
    <div class="split">
      <div class="split-left">
        <div class="script-hdr">
          <h3>채널 선택</h3>
          <div style="display:flex;gap:5px;flex-wrap:wrap">
            <button class="mtab active" id="sch-today"  onclick="switchSCh('today',this)" >📈 오늘경제TV</button>
            <button class="mtab"        id="sch-ripple" onclick="switchSCh('ripple',this)">💎 리플XRP</button>
            <button class="mtab"        id="sch-shiba"  onclick="switchSCh('shiba',this)" >🐕 시바이누</button>
            <button class="mtab"        id="sch-crypto" onclick="switchSCh('crypto',this)">🔮 크립토툰</button>
            <button class="mtab"        id="sch-stock"  onclick="switchSCh('stock',this)" >🖌️ 그려보는주식</button>
          </div>
        </div>
        <div class="script-area">
          <textarea class="script" id="sc-output" placeholder="대본이 여기에 생성됩니다...&#10;&#10;생성 후 → TTS 탭으로 보내기 버튼을 누르세요" readonly></textarea>
        </div>
        <div class="script-foot">
          <span class="badge" id="sc-chars">0자</span>
          <span class="badge" id="sc-paras">0문단</span>
          <span style="flex:1"></span>
          <button class="btn btn-ghost" onclick="scSendToTTS()" id="sc-send-btn" style="display:none">→ TTS로 보내기</button>
          <button class="btn btn-ghost" onclick="scCopy()" id="sc-copy-btn" style="display:none">복사</button>
        </div>
      </div>
      <div class="split-right w360">
        <div class="panel-scroll" id="sc-panel-scroll">
          <div class="panel-sec">
            <div class="panel-hdr">🔑 Gemini API 키</div>
            <div class="panel-body">
              <div class="field-row"><input class="fi" type="password" id="sc-apikey" placeholder="AIza... (설정탭 키 자동사용)" /></div>
              <div style="font-size:10px;color:var(--tx3)">설정탭에 Gemini 키 저장 시 자동 불러옴</div>
            </div>
          </div>
          <div class="panel-sec">
            <div class="panel-hdr">🔗 참고 URL (최대 5개)</div>
            <div class="panel-body">
              <div class="path-row" style="margin-bottom:6px">
                <input class="fi" id="sc-url-inp" placeholder="뉴스/유튜브 URL 입력 후 Enter" onkeydown="if(event.key==='Enter')scAddUrl()" />
                <button class="btn btn-ghost" onclick="scAddUrl()" style="padding:6px 10px">+</button>
              </div>
              <div id="sc-url-chips" style="display:flex;flex-direction:column;gap:4px">
                <div style="font-size:11px;color:var(--tx3)">URL을 추가해주세요</div>
              </div>
            </div>
          </div>
          <div class="panel-sec">
            <div class="panel-hdr">🎯 분석 방향</div>
            <div class="panel-body">
              <div class="field-row"><div class="field-label" id="sc-hint1-lbl">메인 테마 힌트</div><input class="fi" id="sc-hint1" placeholder="비우면 AI 자동 판단" /></div>
              <div class="field-row"><div class="field-label" id="sc-hint2-lbl">소부장 힌트</div><input class="fi" id="sc-hint2" placeholder="비우면 AI 자동 판단" /></div>
              <div class="field-row"><div class="field-label">추가 요청</div><textarea class="fi" id="sc-extra" rows="2" placeholder="추가로 강조할 내용..."></textarea></div>
            </div>
          </div>
          <div class="panel-sec">
            <div class="panel-hdr">🎭 톤</div>
            <div class="panel-body">
              <div class="model-tabs" id="sc-tone-tabs"></div>
            </div>
          </div>
          <div class="panel-sec">
            <div class="panel-hdr">📊 파이프라인</div>
            <div class="panel-body">
              <div id="sc-pipe" style="display:flex;flex-direction:column;gap:2px"></div>
            </div>
          </div>
        </div>
        <div class="panel-foot">
          <button class="btn btn-primary" id="sc-gen-btn" onclick="scGenerate()" style="width:100%;padding:10px">분석 + 대본 생성 ↗</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ═══ TTS ═══ -->
  <div class="page active" id="page-tts">
    <div class="split">
      <div class="split-left">
        <div class="script-hdr">
          <h3>📝 대본</h3><span class="badge" id="tts-badge">0 구간</span>
          <button class="btn btn-ghost" onclick="loadFile('tts-script')" style="font-size:11px;padding:3px 9px">파일</button>
          <button class="btn btn-ghost" onclick="clearTA('tts-script')" style="font-size:11px;padding:3px 9px">지우기</button>
        </div>
        <div class="script-area"><textarea class="script" id="tts-script" placeholder="대본을 입력하세요...&#10;&#10;구분자: ---&lt; 또는 --- (하이픈 3개 이상)"></textarea></div>
        <div class="script-foot">
          <span style="font-size:11px;color:var(--tx3)" id="tts-chars">0자</span><span style="color:var(--tx3)">·</span>
          <span style="font-size:11px;color:var(--tx3)" id="tts-scenes">구간 0개</span>
          <div style="flex:1"></div>
          <button class="btn btn-ghost" onclick="ttsPreview()" style="font-size:11px;padding:5px 12px">▷ 미리듣기</button>
        </div>
      </div>
      <div class="split-right">
        <div class="panel-scroll">
          <div class="panel-sec"><div class="panel-hdr">🎤 목소리</div><div class="panel-body">
            <button class="voice-btn" id="tts-voice-btn" onclick="openVoiceModal('tts')">
              <div class="vb-av" id="tts-vav" style="background:#5b9cf6">?</div>
              <div class="vb-info"><div class="vb-name" id="tts-vname">목소리 선택</div><div class="vb-meta" id="tts-vmeta">라이브러리에서 선택</div></div>
              <span style="color:var(--tx3);font-size:10px">▼</span>
            </button>
          </div></div>
          <div class="panel-sec"><div class="panel-hdr">🤖 모델</div><div class="panel-body"><div class="model-tabs" id="tts-model-tabs"></div></div></div>
          <div class="panel-sec"><div class="panel-hdr">🎛️ 파라미터</div><div class="panel-body">
            <div class="slider-row"><div class="slider-lbl"><span>Stability</span><span class="val" id="v-stab">0.50</span></div><input type="range" min="0" max="1" step="0.01" value="0.5" id="sl-stab" oninput="$('v-stab').textContent=parseFloat(this.value).toFixed(2)"><div class="slider-hints"><span>다양하게</span><span>안정적으로</span></div></div>
            <div class="slider-row"><div class="slider-lbl"><span>Similarity Boost</span><span class="val" id="v-sim">0.75</span></div><input type="range" min="0" max="1" step="0.01" value="0.75" id="sl-sim" oninput="$('v-sim').textContent=parseFloat(this.value).toFixed(2)"><div class="slider-hints"><span>낮음</span><span>높음</span></div></div>
            <div class="slider-row"><div class="slider-lbl"><span>Style</span><span class="val" id="v-sty">0.00</span></div><input type="range" min="0" max="1" step="0.01" value="0" id="sl-sty" oninput="$('v-sty').textContent=parseFloat(this.value).toFixed(2)"></div>
            <div class="slider-row"><div class="slider-lbl"><span>Speed</span><span class="val" id="v-spd">1.00</span></div><input type="range" min="0.7" max="1.3" step="0.01" value="1.0" id="sl-spd" oninput="$('v-spd').textContent=parseFloat(this.value).toFixed(2)"><div class="slider-hints"><span>0.7×</span><span>1.3×</span></div><div style="font-size:10px;color:var(--acc);margin-top:2px" id="tts-speed-info"></div></div>
            <div class="toggle-row"><span class="toggle-label">Speaker Boost</span><label class="toggle"><input type="checkbox" id="tg-spkbst" checked><span class="toggle-sl"></span></label></div>
            <div style="font-size:10px;color:var(--tx3);margin-top:2px;text-align:right" id="tts-maxchar-info"></div>
          </div></div>
          <div class="panel-sec"><div class="panel-hdr">📁 출력</div><div class="panel-body">
            <div class="toggle-row" style="margin-bottom:10px"><span class="toggle-label">구분자마다 파일 분리</span><label class="toggle"><input type="checkbox" id="tg-split" checked><span class="toggle-sl"></span></label></div>
            <div class="field-row"><div class="field-label">포맷</div><select class="fi" id="tts-fmt"><option value="mp3_44100_128">MP3 44.1kHz 128kbps</option><option value="mp3_44100_192">MP3 44.1kHz 192kbps</option><option value="pcm_24000">PCM 24kHz</option></select></div>
            <div class="field-row"><div class="field-label">저장 폴더</div><div class="path-row"><input class="fi" id="tts-outdir" value="studio_output" readonly onclick="pickFolder('tts-outdir')"><button class="btn-icon" onclick="pickFolder('tts-outdir')">🗂️</button><button class="btn-icon" onclick="openFolder('tts-outdir')">📂</button></div></div>
            <div class="field-row2"><div class="field-row"><div class="field-label">프로젝트명</div><input class="fi" id="tts-project" placeholder="1강_AI소개"></div><div class="field-row"><div class="field-label">파일 접두어</div><input class="fi" id="tts-prefix" value="scene"></div></div>
          </div></div>
          <div class="panel-sec"><div class="panel-hdr">✨ Enhance</div><div class="panel-body">
            <div class="toggle-row"><div><span class="toggle-label">Speech Enhancement</span><div style="font-size:10px;color:var(--tx3);margin-top:2px" id="tts-enhance-desc">v3: AI가 감정 태그 자동 삽입 / 나머지: 앞뒤 문맥 연결</div></div><label class="toggle"><input type="checkbox" id="tg-enhance"><span class="toggle-sl"></span></label></div>
          </div></div>
        </div>
        <div class="panel-foot">
          <div class="prog-wrap" id="tts-prog"><div class="prog-hdr"><span class="prog-lbl" id="tts-prog-lbl">생성 중...</span><span class="prog-pct" id="tts-prog-pct">0%</span></div><div class="prog-bar"><div class="prog-fill" id="tts-prog-fill"></div></div></div>
          <div class="log-box" id="tts-log"></div>
          <div style="display:flex;gap:8px"><button class="gen-btn" id="tts-gen-btn" onclick="ttsGenerate()">▶ 음성 생성</button><button class="btn btn-danger" id="tts-cancel-btn" onclick="doCancel()" style="display:none;padding:0 14px">✕</button></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ═══ IMAGE ═══ -->
  <div class="page" id="page-img">
    <div class="split">
      <div class="split-left">
        <div class="script-hdr">
          <h3>📝 대본</h3><span class="badge" id="img-badge">0 구간</span>
          <button class="btn btn-ghost" onclick="loadFile('img-script')" style="font-size:11px;padding:3px 9px">파일</button>
          <button class="btn btn-ghost" onclick="clearTA('img-script')" style="font-size:11px;padding:3px 9px">지우기</button>
        </div>
        <div class="script-area"><textarea class="script" id="img-script" placeholder="구분자(---&lt;)마다 이미지 1장 생성됩니다."></textarea></div>
        <div class="script-foot"><span style="font-size:11px;color:var(--tx3)" id="img-chars">0자</span><span style="color:var(--tx3)">·</span><span style="font-size:11px;color:var(--tx3)" id="img-scenes">구간 0개</span></div>
      </div>
      <div class="split-right w360">
        <div class="panel-scroll">
          <div class="panel-sec"><div class="panel-hdr">🎨 화풍</div><div class="panel-body"><div class="style-grid" id="img-style-grid"></div></div></div>
          <div class="panel-sec" id="custom-prompt-sec" style="display:none"><div class="panel-hdr">✏️ 커스텀 프롬프트</div><div class="panel-body"><textarea class="fi" id="img-custom-prompt" rows="4" placeholder="Art Style: ..."></textarea></div></div>
          <div class="panel-sec"><div class="panel-hdr">🖼️ 레퍼런스 이미지</div><div class="panel-body">
            <div class="ref-drop" id="img-ref-drop" onclick="$('img-ref-input').click()" ondrop="handleRefDrop(event,'img')" ondragover="event.preventDefault()">
              <input type="file" id="img-ref-input" accept="image/*" onchange="handleRefFile(this,'img')" style="display:none">
              <div id="img-ref-text" style="font-size:11px;color:var(--tx2)">클릭 또는 드롭 — 화풍 참고 이미지</div>
              <img id="img-ref-thumb" class="ref-thumb">
            </div>
            <button class="btn btn-ghost" onclick="clearRef('img')" style="font-size:10px;margin-top:6px;width:100%">레퍼런스 지우기</button>
          </div></div>
          <div class="panel-sec"><div class="panel-hdr">📁 출력</div><div class="panel-body">
            <div class="field-row"><div class="field-label">저장 폴더</div><div class="path-row"><input class="fi" id="img-outdir" value="studio_output" readonly onclick="pickFolder('img-outdir')"><button class="btn-icon" onclick="pickFolder('img-outdir')">🗂️</button><button class="btn-icon" onclick="openFolder('img-outdir')">📂</button></div></div>
            <div class="field-row"><div class="field-label">프로젝트명</div><input class="fi" id="img-project" placeholder="뉴스_썸네일"></div>
          </div></div>
        </div>
        <div class="panel-foot">
          <div class="prog-wrap" id="img-prog"><div class="prog-hdr"><span class="prog-lbl" id="img-prog-lbl">생성 중...</span><span class="prog-pct" id="img-prog-pct">0%</span></div><div class="prog-bar"><div class="prog-fill" id="img-prog-fill"></div></div></div>
          <div class="log-box" id="img-log"></div>
          <div style="display:flex;gap:8px"><button class="gen-btn" id="img-gen-btn" onclick="imgGenerate()">🎨 이미지 생성</button><button class="btn btn-danger" id="img-cancel-btn" onclick="doCancel()" style="display:none;padding:0 14px">✕</button></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ═══ COMBO ═══ -->
  <div class="page" id="page-combo">
    <div class="split">
      <div class="split-left">
        <div class="script-hdr">
          <h3>📝 대본</h3><span class="badge" id="combo-badge">0 구간</span>
          <button class="btn btn-ghost" onclick="loadFile('combo-script')" style="font-size:11px;padding:3px 9px">파일</button>
          <button class="btn btn-ghost" onclick="clearTA('combo-script')" style="font-size:11px;padding:3px 9px">지우기</button>
        </div>
        <div class="script-area"><textarea class="script" id="combo-script" placeholder="구분자(---&lt;)마다 이미지 1장 + 음성 1개가 쌍으로 생성됩니다.&#10;&#10;예시:&#10;트럼프가 비트코인을 전략 비축자산으로 선언했습니다. 시장은 즉각 반응했습니다.&#10;&#10;---&lt;&#10;&#10;비트코인 가격이 10만 달러를 돌파하며 전 세계 투자자들이 환호했습니다."></textarea></div>
        <div class="script-foot">
          <span style="font-size:11px;color:var(--tx3)" id="combo-chars">0자</span><span style="color:var(--tx3)">·</span>
          <span style="font-size:11px;color:var(--tx3)" id="combo-scenes">구간 0개</span>
          <div style="flex:1"></div>
          <button class="btn btn-ghost" onclick="comboPreview()" style="font-size:11px;padding:5px 12px">▷ 미리듣기</button>
          <span style="font-size:11px;color:var(--tx2)">구간마다: 이미지 1장 + 음성 1개</span>
        </div>
      </div>
      <div class="split-right w360">
        <div class="panel-scroll">
          <!-- 목소리 -->
          <div class="panel-sec"><div class="panel-hdr">🎤 목소리 (TTS)</div><div class="panel-body">
            <button class="voice-btn" id="combo-voice-btn" onclick="openVoiceModal('combo')">
              <div class="vb-av" id="combo-vav" style="background:#5b9cf6">?</div>
              <div class="vb-info"><div class="vb-name" id="combo-vname">목소리 선택</div><div class="vb-meta" id="combo-vmeta">라이브러리에서 선택</div></div>
              <span style="color:var(--tx3);font-size:10px">▼</span>
            </button>
          </div></div>
          <div class="panel-sec"><div class="panel-hdr">🤖 TTS 모델</div><div class="panel-body"><div class="model-tabs" id="combo-model-tabs"></div></div></div>
          <div class="panel-sec"><div class="panel-hdr">🎛️ 음성 파라미터</div><div class="panel-body">
            <div class="slider-row"><div class="slider-lbl"><span>Stability</span><span class="val" id="cv-stab">0.50</span></div><input type="range" min="0" max="1" step="0.01" value="0.5" id="csl-stab" oninput="$('cv-stab').textContent=parseFloat(this.value).toFixed(2)"></div>
            <div class="slider-row"><div class="slider-lbl"><span>Similarity</span><span class="val" id="cv-sim">0.75</span></div><input type="range" min="0" max="1" step="0.01" value="0.75" id="csl-sim" oninput="$('cv-sim').textContent=parseFloat(this.value).toFixed(2)"></div>
            <div class="slider-row"><div class="slider-lbl"><span>Speed</span><span class="val" id="cv-spd">1.00</span></div><input type="range" min="0.7" max="1.3" step="0.01" value="1.0" id="csl-spd" oninput="$('cv-spd').textContent=parseFloat(this.value).toFixed(2)"><div style="font-size:10px;color:var(--acc);margin-top:2px" id="combo-speed-info"></div></div>
            <div class="toggle-row"><span class="toggle-label">Speaker Boost</span><label class="toggle"><input type="checkbox" id="ctg-spkbst" checked><span class="toggle-sl"></span></label></div>
            <div style="font-size:10px;color:var(--tx3);margin-top:2px;text-align:right" id="combo-maxchar-info"></div>
            <div class="toggle-row"><div><span class="toggle-label">Enhance</span><div style="font-size:10px;color:var(--tx3);margin-top:1px" id="combo-enhance-desc">v3: AI가 감정 태그 자동 삽입 / 나머지: 앞뒤 문맥 연결</div></div><label class="toggle"><input type="checkbox" id="ctg-enhance"><span class="toggle-sl"></span></label></div>
          </div></div>
          <!-- 화풍 -->
          <div class="panel-sec"><div class="panel-hdr">🎨 이미지 화풍</div><div class="panel-body"><div class="style-grid" id="combo-style-grid"></div></div></div>
          <div class="panel-sec" id="combo-custom-sec" style="display:none"><div class="panel-hdr">✏️ 커스텀 프롬프트</div><div class="panel-body"><textarea class="fi" id="combo-custom-prompt" rows="3" placeholder="Art Style: ..."></textarea></div></div>
          <!-- 레퍼런스 이미지 -->
          <div class="panel-sec"><div class="panel-hdr">🖼️ 레퍼런스 이미지</div><div class="panel-body">
            <div class="ref-drop" id="combo-ref-drop" onclick="$('combo-ref-input').click()" ondrop="handleRefDrop(event,'combo')" ondragover="event.preventDefault()">
              <input type="file" id="combo-ref-input" accept="image/*" onchange="handleRefFile(this,'combo')" style="display:none">
              <div id="combo-ref-text" style="font-size:11px;color:var(--tx2)">클릭 또는 드롭 — 화풍 참고 이미지</div>
              <img id="combo-ref-thumb" class="ref-thumb">
            </div>
            <button class="btn btn-ghost" onclick="clearRef('combo')" style="font-size:10px;margin-top:6px;width:100%">레퍼런스 지우기</button>
          </div></div>
          <!-- 출력 -->
          <div class="panel-sec"><div class="panel-hdr">📁 출력</div><div class="panel-body">
            <div class="field-row"><div class="field-label">포맷</div><select class="fi" id="combo-fmt"><option value="mp3_44100_128">MP3 44.1kHz 128kbps</option><option value="mp3_44100_192">MP3 44.1kHz 192kbps</option><option value="pcm_24000">PCM 24kHz</option></select></div>
            <div class="field-row"><div class="field-label">저장 폴더</div><div class="path-row"><input class="fi" id="combo-outdir" value="studio_output" readonly onclick="pickFolder('combo-outdir')"><button class="btn-icon" onclick="pickFolder('combo-outdir')">🗂️</button><button class="btn-icon" onclick="openFolder('combo-outdir')">📂</button></div><div style="font-size:10px;color:var(--tx3);margin-top:3px">→ 폴더 / images/ + audio/ 로 자동 분리</div></div>
            <div class="field-row2"><div class="field-row"><div class="field-label">프로젝트명</div><input class="fi" id="combo-project" placeholder="BTC강의_1화"></div><div class="field-row"><div class="field-label">파일 접두어</div><input class="fi" id="combo-prefix" value="scene"></div></div>
          </div></div>
        </div>
        <div class="panel-foot">
          <div style="background:var(--acc-dim);border:1px solid rgba(245,166,35,.3);border-radius:var(--r6);padding:8px 10px;margin-bottom:8px;font-size:11px;color:var(--acc)">
            구간마다: 🎨 이미지 생성 → 🎙️ 음성 생성 순으로 처리됩니다
          </div>
          <div class="prog-wrap" id="combo-prog"><div class="prog-hdr"><span class="prog-lbl" id="combo-prog-lbl">생성 중...</span><span class="prog-pct" id="combo-prog-pct">0%</span></div><div class="prog-bar"><div class="prog-fill" id="combo-prog-fill"></div></div></div>
          <div class="log-box" id="combo-log"></div>
          <div style="display:flex;gap:8px"><button class="gen-btn" id="combo-gen-btn" onclick="comboGenerate()">🎬 이미지 + 음성 생성</button><button class="btn btn-danger" id="combo-cancel-btn" onclick="doCancel()" style="display:none;padding:0 14px">✕</button></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ═══ SETTINGS ═══ -->
  <div class="page" id="page-settings">
    <div style="flex:1;overflow-y:auto;padding:24px">
      <div style="max-width:520px">
        <div style="font-size:16px;font-weight:600;margin-bottom:18px">⚙️ API 키 설정</div>
        <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r12);padding:18px;margin-bottom:14px">
          <div style="font-size:13px;font-weight:600;margin-bottom:12px">🎙️ ElevenLabs (TTS + Enhance)</div>
          <div style="display:flex;gap:8px"><input class="fi" id="key-el" type="password" placeholder="sk-..." style="flex:1;font-family:monospace"><button class="btn btn-ghost" onclick="togglePw('key-el')" style="font-size:11px">👁</button><button class="btn btn-primary" onclick="saveKeyUI('elevenlabs','key-el')" style="font-size:11px">저장</button></div>
          <div id="el-user-info" style="margin-top:8px;font-size:11px;color:var(--tx2)"></div>
          <div style="margin-top:6px;font-size:11px"><a href="https://elevenlabs.io/app" target="_blank" style="color:var(--acc)">elevenlabs.io →</a></div>
        </div>
        <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r12);padding:18px;margin-bottom:14px">
          <div style="font-size:13px;font-weight:600;margin-bottom:12px">🎨 Google Gemini (이미지 생성)</div>
          <div style="display:flex;gap:8px"><input class="fi" id="key-gemini" type="password" placeholder="AIza..." style="flex:1;font-family:monospace"><button class="btn btn-ghost" onclick="togglePw('key-gemini')" style="font-size:11px">👁</button><button class="btn btn-primary" onclick="saveKeyUI('gemini','key-gemini')" style="font-size:11px">저장</button></div>
          <div style="margin-top:6px;font-size:11px"><a href="https://aistudio.google.com/apikey" target="_blank" style="color:var(--acc)">aistudio.google.com →</a></div>
        </div>
        <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:var(--r12);padding:18px">
          <div style="font-size:13px;font-weight:600;margin-bottom:8px">ℹ️ 황작가 AI 스튜디오 v2.0</div>
          <div style="font-size:12px;color:var(--tx2);line-height:1.8">
            <div>구분자: <code style="color:var(--acc)">---&lt;</code> 또는 <code style="color:var(--acc)">---</code></div>
            <div>TTS · 이미지(13화풍) · 이미지+음성 동시 생성</div>
            <div style="margin-top:4px;color:var(--tx3)">pip install flask requests google-generativeai pillow</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  </div>
</div>
</div>

<!-- VOICE MODAL -->
<div class="modal-bg" id="voice-modal">
  <div class="modal">
    <div class="modal-hdr"><h3>목소리 선택</h3><button class="modal-close" onclick="closeVoiceModal()">×</button></div>
    <div class="modal-toolbar">
      <div class="search-wrap">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6.5" cy="6.5" r="4.5"/><path d="m10 10 3.5 3.5" stroke-linecap="round"/></svg>
        <input class="search-in" id="voice-search" placeholder="목소리 검색..." oninput="filterVoices()">
      </div>
      <div class="filter-chips" id="filter-chips">
        <button class="chip" data-f="recent" onclick="setVFilter(this,'recent')">⭐ 최근 사용</button>
        <button class="chip active" data-f="all" onclick="setVFilter(this,'all')">전체</button>
        <button class="chip" data-f="premade" onclick="setVFilter(this,'premade')">기본</button>
        <button class="chip" data-f="cloned" onclick="setVFilter(this,'cloned')">복제</button>
        <button class="chip" data-f="male" onclick="setVFilter(this,'male')">남성</button>
        <button class="chip" data-f="female" onclick="setVFilter(this,'female')">여성</button>
      </div>
      <button class="btn btn-ghost" onclick="loadVoices()" style="font-size:11px;margin-left:auto">🔄</button>
    </div>
    <div id="recent-voices-bar" style="padding:8px 14px;border-bottom:1px solid var(--bd);display:none">
      <div style="font-size:10px;color:var(--tx3);margin-bottom:6px;font-weight:600;text-transform:uppercase;letter-spacing:.6px">최근 사용</div>
      <div id="recent-voices-list" style="display:flex;gap:6px;flex-wrap:wrap"></div>
    </div>
    <div class="modal-grid" id="voice-modal-grid"><div style="grid-column:1/-1;text-align:center;padding:30px;color:var(--tx3)">목록 불러오기를 클릭하세요</div></div>
  </div>
</div>
<div class="notif" id="notif"></div>

<script>
const $ = id => document.getElementById(id);
const TAB_ID = sessionStorage.getItem('tab_id') || (()=>{const id='tab_'+Math.random().toString(36).slice(2,9);sessionStorage.setItem('tab_id',id);return id;})();
let voices=[], voiceFilter='all', currentEvt=null, currentAudio=null;
let recentVoiceIds=[]; // 최근 사용 목소리 ID 순서 (최신순)
let selectedVoice={tts:null, combo:null};
let ttsModel='eleven_multilingual_v2', comboModel='eleven_multilingual_v2';
let imgStyle='', comboStyle='';
let styles={};
let refPaths={img:null, combo:null};
let voiceTarget='tts'; // 어느 탭 목소리 선택 중인지

const pageInfo={
  script:  {title:'📝 대본 생성', sub:'Gemini AI로 채널별 맞춤 대본을 자동 생성합니다'},
  tts:     {title:'TTS 음성 생성',          sub:'ElevenLabs API로 대본을 음성으로 변환합니다'},
  img:     {title:'이미지 생성',             sub:'Gemini AI로 화풍 13종 중 선택해 대본을 이미지로 시각화합니다'},
  combo:   {title:'🎬 이미지 + 음성 동시 생성', sub:'구간마다 이미지 1장 + 음성 1개를 쌍으로 생성합니다'},
  settings:{title:'설정 / API 키',           sub:''},
};

function gotoPage(p){
  document.querySelectorAll('.page').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(e=>e.classList.remove('active'));
  $('page-'+p).classList.add('active');
  const nb=$('nav-'+p); if(nb) nb.classList.add('active');
  $('tb-title').textContent=pageInfo[p]?.title||'';
  const subEl=$('tb-sub'); if(subEl) subEl.textContent=pageInfo[p]?.sub||'';
  if(p==='settings') loadSettings();
  if(p==='script') scLoadApiKey();
}

function notify(msg,type=''){const n=$('notif');n.textContent=msg;n.className='notif show'+(type?' '+type:'');setTimeout(()=>n.classList.remove('show'),3200);}
function avatarColor(n){const c=['#5b9cf6','#f5a623','#4caf78','#e05252','#9c6fdb','#e87040','#3cb8b2'];let h=0;for(const x of(n||''))h=(h*31+x.charCodeAt(0))%7;return c[Math.abs(h)];}
function initials(n){const w=(n||'?').split(' ');return w.length>=2?w[0][0]+w[1][0]:(n||'?')[0];}
function countScenes(txt){if(!txt.trim())return 0;return txt.trim().split(/(?:^|\r?\n)\s*-{3,}[-<\s]*(?:\r?\n|$)/m).filter(s=>s.trim()).length;}
function bindTA(id,badgeId,charsId,scenesId){
  const ta=$(id),upd=()=>{
    const t=ta.value.trim(),n=countScenes(t);
    if(badgeId)$(badgeId).textContent=n+' 구간';
    if(charsId)$(charsId).textContent=t.length.toLocaleString()+'자';
    if(scenesId)$(scenesId).textContent='구간 '+n+'개';
  };
  ta.addEventListener('input',upd);upd();
}
function loadFile(taId){const inp=document.createElement('input');inp.type='file';inp.accept='.txt,.md,text/*';inp.onchange=async e=>{const f=e.target.files[0];if(!f)return;$(taId).value=await f.text();$(taId).dispatchEvent(new Event('input'));notify('파일 불러옴: '+f.name);};inp.click();}
function clearTA(taId){if($(taId).value&&!confirm('대본을 지우시겠습니까?'))return;$(taId).value='';$(taId).dispatchEvent(new Event('input'));}
async function pickFolder(id){const cur=$(id).value||'studio_output';const r=await fetch('/api/folder-dialog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_dir:cur})});const d=await r.json();if(d.path){$(id).value=d.path;notify('폴더 선택됨','ok');}}
async function openFolder(id){await fetch('/api/open-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:$(id).value||'studio_output'})});}
function togglePw(id){const e=$(id);e.type=e.type==='password'?'text':'password';}

// REF IMAGE
function handleRefFile(input, ns){
  const f=input.files[0]; if(!f)return;
  // 미리보기 즉시 표시
  const objUrl=URL.createObjectURL(f);
  const thumb=$(ns+'-ref-thumb');
  thumb.src=objUrl; thumb.style.display='block';
  $(ns+'-ref-drop').classList.add('has-img');
  $(ns+'-ref-text').textContent='⏫ 업로드 중...';
  // 서버에 업로드
  const fd=new FormData(); fd.append('file',f);
  fetch('/api/img/upload-ref',{method:'POST',body:fd})
    .then(r=>r.json()).then(d=>{
      if(d.path){
        refPaths[ns]=d.path;
        $(ns+'-ref-text').textContent='✅ '+f.name;
        notify('레퍼런스 이미지 설정됨','ok');
      } else {
        $(ns+'-ref-text').textContent='❌ 업로드 실패';
        notify('업로드 실패','err');
      }
    }).catch(()=>{$(ns+'-ref-text').textContent='❌ 네트워크 오류';notify('업로드 오류','err');});
}
function handleRefDrop(e, ns){
  e.preventDefault(); e.stopPropagation();
  const f=e.dataTransfer.files[0]; if(!f||!f.type.startsWith('image/'))return;
  // DataTransfer를 진짜 input에 할당
  try{
    const dt=new DataTransfer(); dt.items.add(f);
    $(ns+'-ref-input').files=dt.files;
  }catch(ex){}
  handleRefFile({files:[f]},ns);
}
function clearRef(ns){
  refPaths[ns]=null;
  $(ns+'-ref-thumb').style.display='none';
  $(ns+'-ref-drop').classList.remove('has-img');
  $(ns+'-ref-text').textContent='클릭 또는 드롭 — 화풍 참고 이미지';
  notify('레퍼런스 이미지 제거됨');
}

// SSE
function startSSE(logId,progId,pfill,plbl,ppct,cbtn,gbtn,onDone){
  // 이전 연결 닫기
  if(currentEvt){currentEvt.close();currentEvt=null;}
  $(progId).style.display='block';$(logId).style.display='block';$(logId).innerHTML='';
  setProgress(pfill,plbl,ppct,0,'시작 중...');
  $(gbtn).disabled=true;
  $(cbtn).style.display='flex';

  const evt=new EventSource('/api/logs?tab_id='+TAB_ID);
  currentEvt=evt;

  // 안전망: 60초 타임아웃 — 어떤 이유로든 DONE 못 받으면 강제 해제
  const safetyTimer=setTimeout(()=>{
    if(currentEvt===evt){
      evt.close();currentEvt=null;
      $(cbtn).style.display='none';$(gbtn).disabled=false;
      notify('시간 초과 — 버튼 복구됨','err');
    }
  },600000); // 10분

  evt.onmessage=e=>{
    const d=JSON.parse(e.data);
    handleSSE(d.msg||'',logId,pfill,plbl,ppct,cbtn,gbtn,onDone,evt,safetyTimer);
  };
  // 연결 에러 시 버튼 복구
  evt.onerror=()=>{
    clearTimeout(safetyTimer);
    if(currentEvt===evt){evt.close();currentEvt=null;}
    $(cbtn).style.display='none';$(gbtn).disabled=false;
  };
}
function handleSSE(msg,logId,pfill,plbl,ppct,cbtn,gbtn,onDone,evt,safetyTimer){
  const lw=$(logId);
  const add=(txt,cls)=>{const d=document.createElement('div');d.className=cls;d.textContent=txt;lw.appendChild(d);lw.scrollTop=lw.scrollHeight;};
  if(msg.startsWith('PROG:')){const rest=msg.slice(5),pi=rest.indexOf('|'),frac=rest.slice(0,pi),txt=rest.slice(pi+1);const[cur,tot]=frac.split('/').map(Number);setProgress(pfill,plbl,ppct,tot>0?Math.round(cur/tot*100):0,`${cur} / ${tot} 완료`);add(txt,'l-prog');}
  else if(msg.startsWith('OK:'))     add(msg.slice(3),'l-ok');
  else if(msg.startsWith('ERR:'))    add(msg.slice(4),'l-err');
  else if(msg.startsWith('SKIP:'))   add(msg.slice(5),'l-skip');
  else if(msg.startsWith('INFO:'))   add(msg.slice(5),'l-info');
  else if(msg.startsWith('IMG:'))    add(msg.slice(4),'l-info');
  else if(msg.startsWith('CANCEL:')) {add(msg.slice(7),'l-cancel');setProgress(pfill,plbl,ppct,0,'중단됨');clearTimeout(safetyTimer);finishSSE(cbtn,gbtn,evt);}
  else if(msg.startsWith('DONE:')){
    const parts=msg.split('|');const cnt=parts[1],dir=parts[2];
    setProgress(pfill,plbl,ppct,100,`완료! ${cnt}개 저장됨`);
    add(`✅ 완료 → ${dir}`,'l-ok');notify(`${cnt}개 완료!`,'ok');
    clearTimeout(safetyTimer);finishSSE(cbtn,gbtn,evt);if(onDone)onDone(dir);
  }
}
function setProgress(pfill,plbl,ppct,pct,lbl){$(pfill).style.width=pct+'%';$(ppct).textContent=pct+'%';$(plbl).textContent=lbl;}
function finishSSE(cbtn,gbtn,evt){
  const e=evt||currentEvt;
  if(e){e.close();}
  if(currentEvt===e)currentEvt=null;
  $(cbtn).style.display='none';
  $(gbtn).disabled=false;
}
async function doCancel(){
  try{await fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tab_id:TAB_ID})});}catch(e){}
  if(currentEvt){currentEvt.close();currentEvt=null;}
  notify('중단 요청됨');
  setTimeout(()=>{['tts','img','combo'].forEach(p=>{try{$(p+'-cancel-btn').style.display='none';$(p+'-gen-btn').disabled=false;}catch(e){}});},500);
}

// INIT
window.addEventListener('load',()=>{
  // 최근 사용 목소리 복원
  try{const r=localStorage.getItem('hwak_recent_voices');if(r)recentVoiceIds=JSON.parse(r);}catch(e){}
  bindTA('tts-script','tts-badge','tts-chars','tts-scenes');
  bindTA('img-script','img-badge','img-chars','img-scenes');
  bindTA('combo-script','combo-badge','combo-chars','combo-scenes');
  loadKeyStatus(); loadTTSModels(); loadStyles(); scRenderTones();
  // ref image : file input 연결은 onclick에서 직접 처리
});

// KEYS
async function loadKeyStatus(){const r=await fetch('/api/keys');const d=await r.json();['elevenlabs','gemini'].forEach(s=>{const dot=$('dot-'+s);if(dot)dot.className='key-dot '+(d[s]?'ok':'no');});}
async function loadSettings(){
  const r=await fetch('/api/keys');const d=await r.json();
  if(d.elevenlabs&&!$('key-el').value)$('key-el').placeholder='••••••••• (저장됨)';
  if(d.gemini&&!$('key-gemini').value)$('key-gemini').placeholder='••••••••• (저장됨)';
  if(d.elevenlabs){const ur=await fetch('/api/tts/user');const ud=await ur.json();if(!ud.error)$('el-user-info').textContent=`플랜: ${ud.tier} | 남은 글자: ${(ud.char_limit-ud.char_used).toLocaleString()}`;}
}
async function saveKeyUI(service,inputId){const key=$(inputId).value.trim();if(!key){notify('키 입력','err');return;}const r=await fetch('/api/keys',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[service]:key})});const d=await r.json();if(d.ok){notify('저장됨','ok');loadKeyStatus();}else notify('저장 실패','err');}

// TTS
async function loadTTSModels(){
  const r=await fetch('/api/tts/models');const d=await r.json();
  const models=d.models||[];
  ['tts-model-tabs','combo-model-tabs'].forEach((tabId,ti)=>{
    const tabs=$(tabId); tabs.innerHTML='';
    models.forEach(m=>{
      const ns=ti===0?'tts':'combo';
      const curModel=ti===0?ttsModel:comboModel;
      const b=document.createElement('button');
      b.className='mtab'+(m.id===curModel?' active':'');
      b.title=m.desc||'';
      b.textContent=m.name||m.id;
      b.onclick=()=>{
        if(ti===0) ttsModel=m.id; else comboModel=m.id;
        tabs.querySelectorAll('.mtab').forEach(x=>x.classList.remove('active'));
        b.classList.add('active');
        applyModelConstraints(ns, m);
      };
      tabs.appendChild(b);
    });
    // 초기 UI 상태 적용
    const curMeta=models.find(m=>m.id===(ti===0?ttsModel:comboModel))||models[0];
    if(curMeta) applyModelConstraints(ti===0?'tts':'combo', curMeta);
  });
}

function applyModelConstraints(ns, meta){
  // Speed 슬라이더
  const speedRow = ns==='tts' ? $('sl-spd')?.closest('.slider-row') : $('csl-spd')?.closest('.slider-row');
  const speedInfo= ns==='tts' ? 'tts-speed-info' : 'combo-speed-info';
  const slId     = ns==='tts' ? 'sl-spd' : 'csl-spd';

  if($(slId)){
    if(!meta.supports_speed_slider){
      $(slId).disabled=true;
      if($(speedInfo)) $(speedInfo).textContent='v3: [slowly]/[quickly] 태그로 자동 변환됩니다';
    } else {
      $(slId).disabled=false;
      if($(speedInfo)) $(speedInfo).textContent='';
    }
  }

  // Speaker Boost
  const sbId = ns==='tts' ? 'tg-spkbst' : 'ctg-spkbst';
  if($(sbId)){
    $(sbId).disabled = !meta.supports_speaker_boost;
    const row=$(sbId).closest('.toggle-row');
    if(row) row.style.opacity = meta.supports_speaker_boost ? '1' : '0.35';
    if(!meta.supports_speaker_boost) $(sbId).checked=false;
  }

  // 최대 글자수 안내
  const charInfo = ns==='tts' ? 'tts-maxchar-info' : 'combo-maxchar-info';
  if($(charInfo)) $(charInfo).textContent = `최대 ${(meta.max_chars||40000).toLocaleString()}자`;

  // Style 슬라이더
  const styId = ns==='tts' ? 'sl-sty' : null;
  if(styId && $(styId)){
    $(styId).disabled = !meta.supports_style;
    const row2=$(styId).closest('.slider-row');
    if(row2) row2.style.opacity=meta.supports_style?'1':'0.35';
  }
  // Enhance 설명 동적 변경
  const enhDescId = ns+'-enhance-desc';
  if($(enhDescId)){
    const isV3 = meta.id==='eleven_v3';
    $(enhDescId).textContent = isV3
      ? '✨ AI가 대본 읽고 [happy][excited] 등 감정 태그 자동 삽입 (Gemini 키 필요)'
      : '앞뒤 문맥 주입 → 억양·강세 자연스럽게 연결';
    $(enhDescId).style.color = isV3 ? 'var(--acc)' : 'var(--tx3)';
  }
}
async function ttsGenerate(){
  const script=$('tts-script').value.trim();if(!script){notify('대본 입력','err');return;}
  if(!selectedVoice.tts){notify('목소리 선택','err');return;}
  startSSE('tts-log','tts-prog','tts-prog-fill','tts-prog-lbl','tts-prog-pct','tts-cancel-btn','tts-gen-btn');
  const r=await fetch('/api/tts/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    tab_id:TAB_ID,script,voice_id:selectedVoice.tts.voice_id,model_id:ttsModel,
    stability:parseFloat($('sl-stab').value),similarity_boost:parseFloat($('sl-sim').value),
    style:parseFloat($('sl-sty').value),use_speaker_boost:$('tg-spkbst').checked,
    speed:parseFloat($('sl-spd').value),output_format:$('tts-fmt').value,
    split_mode:$('tg-split').checked,enhance:$('tg-enhance').checked,
    prefix:$('tts-prefix').value.trim()||'scene',out_dir:$('tts-outdir').value||'studio_output',
    project:$('tts-project').value.trim(),
  })});
  const d=await r.json();if(d.error){notify(d.error,'err');finishSSE($('tts-cancel-btn'),$('tts-gen-btn'));}
}
async function ttsPreview(){
  if(!selectedVoice.tts){notify('목소리 먼저 선택','err');return;}
  const script=$('tts-script').value.trim();if(!script){notify('대본 입력','err');return;}
  const scenes=script.split(/(?:^|\r?\n)\s*-{3,}[-<\s]*(?:\r?\n|$)/m).map(s=>s.trim()).filter(Boolean);
  notify('미리듣기 생성 중...');
  try{
    const r=await fetch('/api/tts/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:scenes[0].slice(0,500),voice_id:selectedVoice.tts.voice_id,model_id:ttsModel,stability:parseFloat($('sl-stab').value),similarity_boost:parseFloat($('sl-sim').value),style:parseFloat($('sl-sty').value),use_speaker_boost:$('tg-spkbst').checked,speed:parseFloat($('sl-spd').value),output_format:$('tts-fmt').value})});
    if(!r.ok){const d=await r.json();notify(d.error||'실패','err');return;}
    const blob=await r.blob();if(currentAudio)currentAudio.pause();
    currentAudio=new Audio(URL.createObjectURL(blob));currentAudio.play();notify('재생 중...','ok');
  }catch(e){notify('미리듣기 실패: '+e,'err');}
}


// ═══ 대본 생성 탭 JS ═══════════════════════════════════════════
const SC_CHANNELS = {
  today:  { name:'오늘경제TV', type:'stock',
    tones:['전문·분석적','긴박·위기감','쉽고 친근한','냉철·객관적','기회·희망적'],
    toneVals:['전문적이고 분석적인','위기감과 긴장감 있는','쉽고 친근한 설명체','냉철하고 객관적인','기회를 강조하는 희망적인'],
    hints:['메인 테마 (예: 방산, AI)','소부장 힌트 (예: 반도체 소재)'],
    sample:`여러분, 요즘 주변에서 반도체, AI 이야기로 돈 벌었다는 소리는 들리는데, 막상 내 주식 계좌를 열어보면 파란 불만 가득해서 속상하신 분들 참 많으시죠? 반가워요, 여러분의 계좌를 확실하게 심폐소생술 해드릴 '오늘경제TV' 채널이에요!

---<

첫 번째로 여러분이 포트폴리오의 중심에 묵직하게 박아두셔야 할 메인 장비주는 바로 '오로스테크놀로지'라는 기업이에요. HBM 층과 층 사이의 오버레이 계측 장비를 국내 유일하게 만드는 무시무시한 기술력을 가진 회사인 거예요.`,
    step3:(t,n)=>`너는 20년 경력 한국 주식 전문 애널리스트야. 반드시 실제 코스피/코스닥 상장 종목 실명으로 추천. "A사","B사" 절대 금지.\n테마: ${t}\n시황: ${n?.substring(0,300)}\n후보 4~5개, 5가지 검증(테마연관성/실적모멘텀/수급/업사이드/리스크) 후 최종 3~4개.\n마크다운 없이 순수 JSON만:\n{"candidates":[{"name":"실제종목명","code":"6자리코드","verifications":{"theme_relevance":{"pass":true,"reason":""},"earnings_momentum":{"pass":true,"reason":""},"supply_demand":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"3문장이유","investment_point":"핵심포인트"}]}`,
    step4:(s,n)=>`너는 한국 소부장 전문 애널리스트야. 실제 상장 소부장 종목 실명 추천. "A사" 금지.\n분야: ${s}\n시황: ${n?.substring(0,300)}\n후보 3~4개, 5가지 검증(공급망연관성/수주실적/기술경쟁력/업사이드/리스크) 후 2~3개.\n마크다운 없이 순수 JSON만:\n{"candidates":[{"name":"실제종목명","code":"6자리코드","verifications":{"supply_chain":{"pass":true,"reason":""},"order_history":{"pass":true,"reason":""},"tech_competency":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"3문장이유","investment_point":"핵심포인트"}]}`,
    scriptPrompt:(d)=>`너는 '오늘경제TV' 유튜브 전문 대본 작가야.\n[참고예시]\n${d.sample}\n[스타일] "~거든요","~잖아요","~인 거예요" 구어체. 일상 비유 필수. 실제 종목명+코드 명시. "A사" 금지.\n[시황]${d.narrative}\n[포인트]${d.points}\n[메인테마주]${d.main}\n[소부장]${d.sobu}\n[추가]${d.extra||'없음'}\n[규칙] 글자수5500~7700. ---< 로 문단구분. 24~30문단. 톤:${d.tone}. ---< 부터 바로시작`,
  },
  ripple: { name:'리플XRP', type:'coin',
    tones:['소름돋는분석','긴박·위기감','희망적강세','음모론시각','냉철·팩트'],
    toneVals:['소름 돋는 분석과 예언적인','위기감과 긴장감이 넘치는','희망적이고 강세론적인','음모론적 시각의','냉철하고 팩트 기반의'],
    hints:['관련 코인/테마 (예: XRP ETF)','분석 포인트 (예: 기관 매집)'],
    sample:`안녕하세요, 반갑습니다! 오늘도 우리 구독자 여러분의 소중한 자산을 지켜드리고, 돈의 흐름을 꿰뚫어 볼 수 있는 아주 뜨겁고 싱싱한 소식들 가득 들고 왔거든요. 지금 전 세계적으로 부채 위기와 달러 가치 하락, 그리고 블록체인 기반의 새로운 시대가 오고 있는데, 우리 엑스알피(XRP)가 그 중심에서 어떤 역할을 하게 될지 오늘 아주 낱낱이 파헤쳐 드릴게요.

---<

지금 중동 리스크가 금융 시스템 붕괴를 가속하는 의도적인 연극이라는 시각이 있는데, 사실 보이지 않는 곳에서는 무역 협정과 디지털 결제 인프라 개발이라는 거대한 전환이 소리 없이 이루어지고 있거든요. 오늘 이 영상에서는 왜 금융 시스템이 무너지고 있는지, 그리고 시스템 재편에 왜 리플이 절대적으로 필요한지 그 소름 돋는 이유를 아주 명쾌하게 분석해 드릴게요.

---<

본격적으로 진짜 돈의 설계도를 파헤치기 전에, 오늘 내용이 여러분 투자에 조금이라도 인사이트를 드린다면 좋아요와 구독 한 번씩만 꾹 부탁드릴게요. 여러분의 따뜻한 응원 클릭 한 번이 매일 밤새워 글로벌 뉴스를 분석하고 데이터를 뜯어보는 저한테 정말 엄청난 에너지가 되거든요. 아, 그리고 영상 마지막에는 여러분의 투자 안목을 완전히 바꿔줄 소정의 선물도 준비했으니까 끝까지 집중해서 시청해 주세요! 자, 이제 리플이 앞으로 어떻게 될지, 그 운명을 결정지을 진짜 이야기 시작합니다.

---<

가장 먼저 우리 머릿속을 복잡하게 만드는 '시가총액'의 환상부터 깨야 해요. 뉴스에서 시총이 너무 커서 절대 비싸질 수 없다고 말하는 건, 사실 우리 아파트 단지에 집이 천 채가 있는데 어제 딱 한 집이 비싸게 팔렸다고 해서 모든 집주인 주머니에 현금이 꽉 차 있다고 착각하는 거랑 똑같은 거거든요. 실제로는 시장에 그만큼의 현금이 없어도 호가창에 물량이 마르는 '공급 쇼크'가 터지면 가격은 얼마든지 수직으로 솟구칠 수 있거든요.

---<

지금 일본의 엔캐리 청산 이슈가 아주 뜨거운데, 0% 금리로 돈을 빌려 월스트리트 국채를 사던 시대가 저물고 엔화 가치가 오르기 시작했어요. 유럽과 미국 자본이 일본으로 돌아가면 달러 가치는 떨어지고 국채 매도가 가속화될 수밖에 없거든요. 이런 미국의 경제 대공황과 인플레이션 위기 속에서, 기관들은 이미 비트코인을 제치고 엑스알피를 새로운 타깃으로 삼기 시작했습니다.`,
    step3:(t,n)=>`너는 암호화폐 전문 분석가야. XRP/리플 관련 코인 분석.\n테마:${t||'XRP 리플'}\n시황:${n?.substring(0,300)}\n후보3~4개, 5가지 검증(XRP생태계연관성/규제명확성/기관수급/업사이드/리스크).\n순수 JSON: {"candidates":[{"name":"코인명","code":"티커","verifications":{"theme_relevance":{"pass":true,"reason":""},"earnings_momentum":{"pass":true,"reason":""},"supply_demand":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    step4:(s,n)=>`너는 XRPL 생태계 전문가야. XRPL 기반 프로젝트 분석.\n분야:${s||'XRPL DeFi'}\n시황:${n?.substring(0,300)}\n후보2~3개. 순수 JSON: {"candidates":[{"name":"프로젝트명","code":"티커","verifications":{"supply_chain":{"pass":true,"reason":""},"order_history":{"pass":true,"reason":""},"tech_competency":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    scriptPrompt:(d)=>`너는 리플XRP 전문 유튜브 채널 대본 작가야.\n[참고예시]\n${d.sample}\n[스타일] 거시경제→XRP연결 필수. "소름 돋는","완벽한" 강한 표현. 세력 의도 분석.\n[시황]${d.narrative}\n[코인분석]${d.main}\n[생태계]${d.sobu}\n[추가]${d.extra||'없음'}\n[규칙] 글자수5500~7700. ---< 문단구분. 24~30문단. 톤:${d.tone}. ---< 부터시작`,
  },
  shiba:  { name:'시바이누', type:'coin',
    tones:['개미공감형','긴박·위기감','강세론·희망','세력분석','역발상투자'],
    toneVals:['개미 심리에 공감하는','위기감과 긴장감이 넘치는','강세론적이고 희망적인','세력의 의도를 분석하는','역발상 투자를 강조하는'],
    hints:['관련 테마 (예: SHIB 소각)','분석 포인트 (예: 시바리움)'],
    sample:`피곤한 눈 비벼가며 아무리 차트 공부를 해봐도, 결국 작은 파동에 멘탈이 나가 덜덜 떨며 매도 버튼을 누르고 마는 게 우리 평범한 개미들의 슬픈 현실이잖아요. 팔고 나면 보란 듯이 위로 솟구치는 빨간 기둥을 보면서 뜬눈으로 밤을 지새우는 이 지독하고 억울한 패턴, 이제는 정말 완벽하게 끊어내셔야 해요. 코인판에서 돈을 버는 원리는 진짜 잔인할 정도로 아주 단순하거든요.

---<

바로 세력이 호가창을 짓누르는 '진짜 의도'를 남들보다 딱 반발짝 미리 아는 것, 그거 하나뿐이에요. 최근 영국 BBC가 인정한 '크레이그 해밀턴 파커'라는 예언가가 2026년 암호화폐 시장에 대해 아주 소름 돋는 예언을 내놨거든요. 이 사람이 누구냐면, 과거에 전 세계가 비웃을 때 브렉시트 통과와 트럼프 당선을 정확하게 맞춰서 적중률 85%를 자랑하는 엄청난 인물이에요.

---<

이 엄청난 예언가가 2026년 코인 시장을 두고 이렇게 말했어요. "대규모 정리가 일어날 것이고 수많은 코인들이 흔적도 없이 사라질 것이다. 그런데 그 폭풍 속에서, 모든 사람이 포기했을 때 가장 강하게 돌아오는 코인이 있다." 여기서 가장 중요한 핵심은 바로 "이미 한 번 기적을 만들었던 코인"이라고 콕 집어서 묘사했다는 거예요.

---<

아무도 믿지 않았는데 세상을 완벽하게 뒤집었던 코인, 출시 1년 만에 100만 퍼센트라는 경이로운 상승을 기록했던 코인. 코인 역사상 이 묘사와 정확하게 일치하는 건 시바이누 딱 하나밖에 없잖아요. 대중들은 이 예언을 그저 흥미로운 가십거리나 미신으로 치부하고 넘겨버리지만, 우리는 이걸 철저하게 자본 시장의 '수급과 심리' 관점으로 해석해야 하거든요.

---<

예언가가 말한 "모든 사람이 포기했을 때"라는 문장을 투자 심리학으로 번역해 보면, 바로 '극단적 공포와 거래량 실종' 상태를 의미하는 거예요. 지금 시바이누 커뮤니티나 차트 분위기를 한번 냉정하게 보세요. 검색량은 바닥을 치고 있고, 거래량은 씨가 말랐으며, 대중들은 밈 코인은 끝났다며 욕을 하고 떠나가고 있잖아요.`,
    step3:(t,n)=>`너는 밈코인 전문 분석가야. 시바이누 관련 밈코인 분석.\n테마:${t||'SHIB 시바이누'}\n시황:${n?.substring(0,300)}\n후보3~4개, 5가지 검증(밈파워/소각메커니즘/고래수급/업사이드/리스크).\n순수 JSON: {"candidates":[{"name":"코인명","code":"티커","verifications":{"theme_relevance":{"pass":true,"reason":""},"earnings_momentum":{"pass":true,"reason":""},"supply_demand":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    step4:(s,n)=>`너는 시바리움 생태계 전문가야.\n분야:${s||'시바리움 생태계'}\n시황:${n?.substring(0,300)}\n후보2~3개. 순수 JSON: {"candidates":[{"name":"프로젝트명","code":"티커","verifications":{"supply_chain":{"pass":true,"reason":""},"order_history":{"pass":true,"reason":""},"tech_competency":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    scriptPrompt:(d)=>`너는 시바이누 전문 유튜브 채널 대본 작가야.\n[참고예시]\n${d.sample}\n[스타일] 개미 억울함 공감. 고래 매집 시나리오. 역사적 패턴 비교.\n[시황]${d.narrative}\n[코인분석]${d.main}\n[생태계]${d.sobu}\n[추가]${d.extra||'없음'}\n[규칙] 글자수5500~7700. ---< 문단구분. 24~30문단. 톤:${d.tone}. ---< 부터시작`,
  },
  crypto: { name:'크립토툰', type:'coin',
    tones:['지정학연결','긴박·위기감','스마트머니추적','냉철·팩트','강세론'],
    toneVals:['지정학과 코인을 연결하는','위기감과 긴장감이 넘치는','스마트머니의 움직임을 추적하는','냉철하고 팩트 기반의','강세론적이고 희망적인'],
    hints:['분석 코인 (예: BTC, XRP)','지정학 이슈 (예: 중동, 무역전쟁)'],
    sample:`꽉 막힌 코인판의 흐름을 시원하게 뚫어드리는 시간, 크립토 툰의 정프로 인사드려요. 오늘 아침 뉴스 켜보시고 "아, 이제 진짜 중동에서 3차 대전 터지는 거 아니냐, 내 XRP는 어떡하냐"며 가슴 철렁하신 분들 정말 많으시죠? 오늘 저는 트럼프가 던진 이 거대한 지정학적 폭탄 속에 숨겨진 진짜 세력들의 의도와, 여러분의 XRP가 왜 이 혼돈 속에서 가장 빛나는 황금 동앗줄이 될 수밖에 없는지 아주 낱낱이 까발려 드릴게요.

---<

지금 호가창만 보면 숨이 턱턱 막히고 두려우실 거 다 알아요. 하지만 이럴 때일수록 감정에 휘둘리면 세력들의 완벽한 호구가 될 뿐이거든요. 남들이 공포에 질려 도망칠 때 우리는 차가운 머리로 시장의 본질을 꿰뚫어 봐야 해요.

---<

본격적인 돈 냄새 추적에 들어가기 전에, 이 지독한 공포장 속에서도 멘탈 꽉 잡고 저와 함께 진짜 부의 이동을 끝까지 추적하시겠다는 다짐으로 좋아요와 구독, 알림 설정 한 번씩만 팍팍 눌러주세요. 여러분의 클릭 한 번이 얄팍한 언론의 공포 마케팅에 속지 않고 진짜 팩트만 파헤치는 제게 가장 든든한 무기가 되거든요.

---<

자, 지금 전 세계 자본 시장을 그야말로 패닉에 빠뜨린 엄청난 속보부터 한번 뜯어볼게요. 트럼프 미국 대통령이 미 해군을 동원해서 호르무즈 해협을 드나드는 이란 관련 선박을 전면 봉쇄하는 절차에 즉각 돌입하겠다고 선언해 버렸죠.

---<

오늘 밤 11시를 기점으로 아라비아만과 오만만 등 이란 항구로 오가는 모든 국가의 선박이 강제적인 해상 봉쇄 조치를 받게 돼요. 이란이 얄밉게 유조선 통행료를 뜯어내고 국제 유가를 볼모로 장난치던 꼴을 더 이상 좌시하지 않고, 아예 숨통을 끊어버리겠다는 아주 무시무시한 선전포고를 날린 거예요.`,
    step3:(t,n)=>`너는 지정학 연계 암호화폐 분석 전문가야.\n테마:${t||'BTC 비트코인'}\n시황:${n?.substring(0,300)}\n지정학 이슈 연결 코인 3~4개, 5가지 검증(지정학연관성/기관수급/온체인데이터/업사이드/리스크).\n순수 JSON: {"candidates":[{"name":"코인명","code":"티커","verifications":{"theme_relevance":{"pass":true,"reason":""},"earnings_momentum":{"pass":true,"reason":""},"supply_demand":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    step4:(s,n)=>`너는 블록체인 인프라 전문가야.\n분야:${s||'레이어1/레이어2'}\n시황:${n?.substring(0,300)}\n핵심 인프라 2~3개. 순수 JSON: {"candidates":[{"name":"프로젝트명","code":"티커","verifications":{"supply_chain":{"pass":true,"reason":""},"order_history":{"pass":true,"reason":""},"tech_competency":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    scriptPrompt:(d)=>`너는 크립토툰 유튜브 채널 대본 작가야.\n[참고예시]\n${d.sample}\n[스타일] 반드시 지정학→코인 연결. 공포 마케팅 역발상. 스마트머니 뒷면 분석.\n[시황]${d.narrative}\n[코인분석]${d.main}\n[인프라]${d.sobu}\n[추가]${d.extra||'없음'}\n[규칙] 글자수5500~7700. ---< 문단구분. 24~30문단. 톤:${d.tone}. ---< 부터시작`,
  },
  stock:  { name:'그려보는주식', type:'stock_global',
    tones:['매크로연결분석','긴박·위기감','기회강조','스마트머니추적','냉철·객관적'],
    toneVals:['매크로와 주식을 연결하는','위기감과 긴장감이 넘치는','기회를 강조하는 희망적인','스마트머니의 움직임을 추적하는','냉철하고 객관적인'],
    hints:['분석 종목/테마 (예: 테슬라, 빅테크)','매크로 이슈 (예: 유가, 금리)'],
    sample:`반갑습니다. 복잡한 주식 시장의 얽히고설킨 흐름을 한 폭의 그림처럼 명쾌하게 찢어발겨 드리는 시간, 그려보는 주식의 정프로입니다! 요 며칠 선물 시장이 뚝뚝 떨어지고 기름값이 치솟으면서 "아, 진짜 주식 다 팔고 도망쳐야 하나"라며 밤잠 설치신 분들 정말 많으시죠? 오늘 저는 이 거대한 매크로의 공포 속에 완벽하게 가려져 버린 테슬라의 진짜 미친 호재와, 왜 지금이 빅테크를 쓸어 담아야 할 일생일대의 기회인지 아주 낱낱이 까발려 드릴게요.

---<

본격적인 돈 냄새 추적에 들어가기 전에, 이 지독한 공포장 속에서도 멘탈 꽉 잡고 저와 함께 진짜 부의 이동을 추적하시겠다는 다짐으로 좋아요와 구독, 알림 설정 한 번씩만 팍팍 눌러주세요. 여러분의 클릭 한 번이 얄팍한 언론의 공포 마케팅에 속지 않고 진짜 팩트만 파헤치는 제게 가장 든든한 무기가 되거든요. 자, 지금 전 세계 자본 시장을 짓누르고 있는 거대한 지정학적 노이즈부터 한번 뜯어보겠습니다.

---<

지금 시장 호가창을 보면 그야말로 살얼음판이 따로 없어요. 다우, S&P 500, 나스닥 할 것 없이 선물 시장이 평소의 세 배, 네 배 수준으로 뚝뚝 떨어지고 있죠. 그 이유가 뭘까요? 주말 내내 세상을 시끄럽게 했던 미국과 이란의 휴전 협상이 결국 파투가 나버렸기 때문입니다. 이란은 호르무즈 해협 개방이나 핵무기 양보 같은 핵심 카드들을 끝까지 쥐고 흔들었고, 미국 대표단은 짐을 싸서 고국으로 돌아가 버렸거든요.

---<

협상 결렬의 후폭풍은 생각보다 아주 끔찍합니다. 당장 국제 유가가 미친 듯이 솟구치고 있잖아요. 한때 50달러, 90달러 하던 텍사스 중질유(WTI)가 지금 105달러를 넘어 110달러를 향해 맹렬하게 돌진하고 있습니다. 10년물 국채 금리도 4.36%를 뚫고 올라가 버렸고요. 유가가 뛰면 인플레이션 공포가 살아나고, 금리 인하는 물 건너갔다는 절망감이 시장을 통째로 짓누르는 완벽한 악순환의 고리가 완성된 겁니다.

---<

이런 무시무시한 매크로 환경 속에서 소비자들의 심리마저 꽁꽁 얼어붙었습니다. 미국 소비자 심리지수가 지난달 대비 무려 10% 이상 폭락하며 47포인트대까지 주저앉았어요. 동네 주유소 기름값이 갤런당 6달러, 7달러를 찍는 걸 두 눈으로 보고 있으니 지갑이 열릴 리가 없죠. 트럼프 입장에서도 가을 선거를 앞두고 이 물가 폭등과 이란의 늪에 빠져버린 상황이 엄청난 정치적 부담으로 작용하고 있습니다.`,
    step3:(t,n)=>`너는 글로벌 주식 전문 애널리스트야.\n테마:${t||'빅테크 나스닥'}\n시황:${n?.substring(0,300)}\n매크로 공포 속 저평가 글로벌 주식 3~4개, 5가지 검증(펀더멘털/밸류에이션/기관수급/업사이드/리스크).\n순수 JSON: {"candidates":[{"name":"종목명","code":"티커","verifications":{"theme_relevance":{"pass":true,"reason":""},"earnings_momentum":{"pass":true,"reason":""},"supply_demand":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    step4:(s,n)=>`너는 빅테크 공급망 전문가야.\n분야:${s||'반도체 AI 인프라'}\n시황:${n?.substring(0,300)}\n공급망 핵심 부품주 2~3개. 순수 JSON: {"candidates":[{"name":"종목명","code":"티커","verifications":{"supply_chain":{"pass":true,"reason":""},"order_history":{"pass":true,"reason":""},"tech_competency":{"pass":true,"reason":""},"upside":{"pass":true,"reason":""},"risk_check":{"pass":true,"reason":""}},"final_pick":true,"pick_reason":"이유","investment_point":"포인트"}]}`,
    scriptPrompt:(d)=>`너는 그려보는주식 유튜브 채널 대본 작가야.\n[참고예시]\n${d.sample}\n[스타일] 매크로→주식 연결 필수. 공포 속 숨겨진 호재 역발상. 밸류에이션 바겐세일 논리.\n[시황]${d.narrative}\n[종목분석]${d.main}\n[공급망]${d.sobu}\n[추가]${d.extra||'없음'}\n[규칙] 글자수5500~7700. ---< 문단구분. 24~30문단. 톤:${d.tone}. ---< 부터시작`,
  },
};

let scCh='today', scUrls=[], scSteps=[];

function switchSCh(ch, el) {
  scCh=ch;
  document.querySelectorAll('[id^="sch-"]').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  scRenderTones();
  const cfg=SC_CHANNELS[ch];
  $('sc-hint1-lbl').textContent=cfg.hints[0];
  $('sc-hint2-lbl').textContent=cfg.hints[1];
  $('sc-hint1').placeholder='비우면 AI 자동 판단';
  $('sc-hint2').placeholder='비우면 AI 자동 판단';
  scUrls=[]; scRenderChips();
}

function scLoadApiKey(){
  // 설정탭에 저장된 gemini 키 자동 가져오기
  const stored=localStorage.getItem('hwak_gemini_key')||'';
  if(stored && !$('sc-apikey').value) $('sc-apikey').value=stored;
}

function scRenderTones(){
  const cfg=SC_CHANNELS[scCh];
  $('sc-tone-tabs').innerHTML=cfg.tones.map((t,i)=>
    `<button class="mtab${i===0?' active':''}" data-val="${cfg.toneVals[i]}" onclick="this.parentNode.querySelectorAll('.mtab').forEach(b=>b.classList.remove('active'));this.classList.add('active')">${t}</button>`
  ).join('');
}

function scGetTone(){
  return $('sc-tone-tabs').querySelector('.mtab.active')?.dataset.val || SC_CHANNELS[scCh].toneVals[0];
}

function scAddUrl(){
  const inp=$('sc-url-inp'), val=inp.value.trim();
  if(!val) return;
  if(!val.startsWith('http')){notify('http로 시작하는 URL 입력','err');return;}
  if(scUrls.length>=5){notify('최대 5개','err');return;}
  if(!scUrls.includes(val)) scUrls.push(val);
  scRenderChips(); inp.value='';
}

function scRemoveUrl(i){scUrls.splice(i,1);scRenderChips();}

function scRenderChips(){
  const el=$('sc-url-chips');
  if(!scUrls.length){el.innerHTML='<div style="font-size:11px;color:var(--tx3)">URL을 추가해주세요</div>';return;}
  el.innerHTML=scUrls.map((u,i)=>{
    const yt=u.includes('youtu');
    return `<div style="display:flex;align-items:center;gap:6px;background:var(--bg2);border:1px solid var(--bd);border-radius:6px;padding:4px 8px;font-size:11px">
      <span style="font-size:9px;padding:1px 5px;border-radius:8px;font-weight:700;background:${yt?'rgba(239,68,68,.15)':'rgba(91,156,246,.15)'};color:${yt?'#e05252':'#5b9cf6'}">${yt?'YT':'NEWS'}</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--tx1)">${u.replace(/^https?:\/\//,'').substring(0,40)}…</span>
      <span style="cursor:pointer;color:var(--tx3)" onclick="scRemoveUrl(${i})">×</span>
    </div>`;
  }).join('');
}

function scSetPipe(steps){
  $('sc-pipe').innerHTML=steps.map((s,i)=>{
    let icon='', color='var(--tx3)';
    if(s.state==='wait'){icon=String(i+1);}
    else if(s.state==='run'){icon='↻';color='var(--blue)';}
    else if(s.state==='done'){icon='✓';color='var(--green)';}
    else{icon='!';color='var(--red)';}
    return `<div style="display:flex;gap:8px;align-items:flex-start;padding:6px 0;border-bottom:1px solid var(--bd)">
      <div style="width:20px;height:20px;border-radius:50%;background:var(--bg3);color:${color};display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0;${s.state==='run'?'animation:spin .8s linear infinite':''}">${icon}</div>
      <div style="flex:1">
        <div style="font-size:11px;font-weight:500;color:var(--tx0)">${s.title}</div>
        ${s.detail?`<div style="font-size:10px;color:var(--tx2);margin-top:2px">${s.detail}</div>`:''}
      </div>
    </div>`;
  }).join('');
}

async function scGeminiCall(key, prompt, maxTok){
  const r=await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${key}`,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({contents:[{role:'user',parts:[{text:prompt}]}],generationConfig:{temperature:0.8,maxOutputTokens:maxTok||4096}})
  });
  const d=await r.json();
  if(d.error) throw new Error(d.error.message);
  return d.candidates?.[0]?.content?.parts?.[0]?.text||'';
}

async function scGeminiStream(key, prompt, onChunk){
  const resp=await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse&key=${key}`,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({contents:[{role:'user',parts:[{text:prompt}]}],generationConfig:{temperature:0.85,maxOutputTokens:8192}})
  });
  if(!resp.ok){const e=await resp.json();throw new Error(e.error?.message||'API오류');}
  const reader=resp.body.getReader(),decoder=new TextDecoder();
  let full='';
  while(true){
    const{done,value}=await reader.read();if(done)break;
    for(const line of decoder.decode(value,{stream:true}).split('\n')){
      if(!line.startsWith('data: '))continue;
      const data=line.slice(6).trim();if(!data||data==='[DONE]')continue;
      try{const t=JSON.parse(data).candidates?.[0]?.content?.parts?.[0]?.text;if(t){full+=t;onChunk(full);}}catch(e){}
    }
  }
  return full;
}

async function scGenerate(){
  const apiKey=$('sc-apikey').value.trim()||localStorage.getItem('hwak_gemini_key')||'';
  if(!apiKey){notify('Gemini API 키를 입력해주세요','err');return;}
  if(!scUrls.length){notify('URL을 1개 이상 추가해주세요','err');return;}
  const cfg=SC_CHANNELS[scCh];
  const hint1=$('sc-hint1').value.trim(), hint2=$('sc-hint2').value.trim();
  const extra=$('sc-extra').value.trim(), tone=scGetTone();
  const btn=$('sc-gen-btn');
  btn.disabled=true;btn.textContent='처리 중...';
  $('sc-output').value='';
  $('sc-send-btn').style.display='none';
  $('sc-copy-btn').style.display='none';

  const pipeSteps=[
    {title:'URL 분석',state:'wait',detail:''},
    {title:'시황 재구성',state:'wait',detail:''},
    {title:'메인 분석 5중 검증',state:'wait',detail:''},
    {title:'서브 분야 5중 검증',state:'wait',detail:''},
    {title:'대본 생성',state:'wait',detail:''},
  ];
  const upd=(i,state,detail)=>{pipeSteps[i]={...pipeSteps[i],state,detail};scSetPipe(pipeSteps);};
  scSetPipe(pipeSteps);

  try{
    // STEP 1
    upd(0,'run',`${scUrls.length}개 URL 분석 중...`);
    const s1=await scGeminiCall(apiKey,`다음 URL들의 내용을 분석해 핵심 정보 추출. 채널: ${cfg.name}\nURL:\n${scUrls.map((u,i)=>`[${i+1}] ${u}`).join('\n')}\n마크다운 없이 순수 JSON:\n{"sources":[{"url":"","keywords":[],"implications":"","mentioned_sectors":[]}]}`,2000);
    let srcData; try{srcData=JSON.parse(s1.replace(/\`\`\`json|\`\`\`/g,'').trim());}catch(e){srcData={sources:[]};}
    upd(0,'done',srcData.sources?.map(s=>s.keywords?.slice(0,3).join(', ')).filter(Boolean).join(' / ')||'완료');

    // STEP 2
    upd(1,'run','시황 스토리 구성 중...');
    const s2=await scGeminiCall(apiKey,`소스:${JSON.stringify(srcData)}\n채널:${cfg.name}\n시청자를 위한 시황 스토리 구성. 단순요약 금지, 맥락 풍부하게.\n순수 JSON: {"main_narrative":"400자이상","key_points":[],"suggested_main":"","suggested_sub":""}`,2000);
    let narr; try{narr=JSON.parse(s2.replace(/\`\`\`json|\`\`\`/g,'').trim());}catch(e){narr={main_narrative:s2,key_points:[],suggested_main:hint1,suggested_sub:hint2};}
    const fh1=hint1||narr.suggested_main||'주요 테마';
    const fh2=hint2||narr.suggested_sub||'관련 분야';
    upd(1,'done',`완료 | ${fh1} | ${fh2}`);

    // STEP 3
    upd(2,'run','후보 발굴 + 검증 중...');
    const s3=await scGeminiCall(apiKey,cfg.step3(fh1,narr.main_narrative),3000);
    let mainData; try{mainData=JSON.parse(s3.replace(/\`\`\`json|\`\`\`/g,'').trim());}catch(e){mainData={candidates:[]};}
    const mainPicks=mainData.candidates?.filter(c=>c.final_pick)||mainData.candidates?.slice(0,4)||[];
    upd(2,'done',mainPicks.map(c=>`${c.name}(${c.code})`).join(', ')||'완료');

    // STEP 4
    upd(3,'run','서브 분야 발굴 중...');
    const s4=await scGeminiCall(apiKey,cfg.step4(fh2,narr.main_narrative),3000);
    let sobuData; try{sobuData=JSON.parse(s4.replace(/\`\`\`json|\`\`\`/g,'').trim());}catch(e){sobuData={candidates:[]};}
    const sobuPicks=sobuData.candidates?.filter(c=>c.final_pick)||sobuData.candidates?.slice(0,3)||[];
    upd(3,'done',sobuPicks.map(c=>`${c.name}(${c.code})`).join(', ')||'완료');

    // STEP 5
    upd(4,'run','대본 스트리밍 작성 중...');
    const mainDesc=mainPicks.map(c=>`- ${c.name}(${c.code}): ${c.pick_reason} | ${c.investment_point}`).join('\n');
    const sobuDesc=sobuPicks.map(c=>`- ${c.name}(${c.code}): ${c.pick_reason} | ${c.investment_point}`).join('\n');
    const outEl=$('sc-output');
    const script=await scGeminiStream(apiKey, cfg.scriptPrompt({
      sample:cfg.sample, narrative:narr.main_narrative,
      points:narr.key_points?.join('\n')||'',
      main:mainDesc||'(완료)', sobu:sobuDesc||'(완료)',
      extra, tone,
    }), (p)=>{outEl.value=p;outEl.scrollTop=outEl.scrollHeight;});

    const chars=script.replace(/\s/g,'').length;
    const paras=(script.match(/---</g)||[]).length;
    $('sc-chars').textContent=chars.toLocaleString()+'자';
    $('sc-paras').textContent=paras+'문단';
    $('sc-send-btn').style.display='';
    $('sc-copy-btn').style.display='';
    upd(4,'done',`완료 — ${chars.toLocaleString()}자 / ${paras}문단`);
    notify('대본 생성 완료!','ok');

  }catch(err){
    const ri=pipeSteps.findIndex(s=>s.state==='run');
    if(ri>=0) upd(ri,'err','오류: '+err.message);
    notify('오류: '+err.message,'err');
  }
  btn.disabled=false; btn.textContent='분석 + 대본 생성 ↗';
}

function scSendToTTS(){
  const script=$('sc-output').value;
  if(!script)return;
  $('tts-script').value=script;
  gotoPage('tts');
  notify('TTS 탭으로 대본 전송 완료!','ok');
}

function scCopy(){
  navigator.clipboard.writeText($('sc-output').value).then(()=>notify('복사됨','ok'));
}
// ═══════════════════════════════════════════════════════════════

// VOICES
function openVoiceModal(target){voiceTarget=target;$('voice-modal').classList.add('open');if(voices.length===0)loadVoices();else filterVoices();}
function closeVoiceModal(){$('voice-modal').classList.remove('open');}
$('voice-modal').addEventListener('click',e=>{if(e.target===$('voice-modal'))closeVoiceModal();});
async function loadVoices(){$('voice-modal-grid').innerHTML='<div style="grid-column:1/-1;text-align:center;padding:30px;color:var(--tx3)">불러오는 중...</div>';try{const r=await fetch('/api/tts/voices');const d=await r.json();if(d.error){notify('목소리 로드 실패','err');return;}voices=d.voices||[];filterVoices();notify('목소리 '+voices.length+'개','ok');}catch(e){notify('오류: '+e,'err');}}
function setVFilter(btn,f){document.querySelectorAll('#filter-chips .chip').forEach(b=>b.classList.remove('active'));btn.classList.add('active');voiceFilter=f;filterVoices();}
function voiceCardHTML(v){
  const col=avatarColor(v.name),ini=initials(v.name).toUpperCase();
  const tags=[v.gender,v.age,v.accent,v.use_case].filter(Boolean).map(t=>`<span class="vc-tag">${t}</span>`).join('');
  const sel=selectedVoice[voiceTarget]?.voice_id===v.voice_id;
  return `<div class="voice-card${sel?' sel':''}" onclick="selectVoice('${v.voice_id}')">
    <div class="vc-top"><div class="vc-av" style="background:${col}">${ini}</div><div><div class="vc-name">${v.name}</div><div class="vc-cat">${v.category||''}</div></div></div>
    <div class="vc-tags">${tags||'<span class="vc-tag">기타</span>'}</div>
    <div class="vc-actions">${v.preview_url?`<button class="play-btn" onclick="playPrev('${v.preview_url}',event)">▶</button>`:''}<button class="use-btn" onclick="selectVoice('${v.voice_id}');event.stopPropagation()">선택</button></div>
  </div>`;
}
function filterVoices(){
  const q=($('voice-search').value||'').toLowerCase();
  const grid=$('voice-modal-grid');

  // 최근 사용 필터
  if(voiceFilter==='recent'){
    if(!recentVoiceIds.length){grid.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:30px;color:var(--tx3)">아직 사용한 목소리가 없습니다</div>';return;}
    // 최근 순서대로 목소리 객체 찾기
    const recent=recentVoiceIds.map(id=>voices.find(v=>v.voice_id===id)).filter(Boolean);
    grid.innerHTML=recent.length
      ? `<div style="grid-column:1/-1;font-size:10px;color:var(--tx3);margin-bottom:4px">최근 사용 순서</div>`+recent.map(v=>voiceCardHTML(v)).join('')
      : '<div style="grid-column:1/-1;text-align:center;padding:30px;color:var(--tx3)">목소리를 불러온 후 사용해보세요</div>';
    return;
  }

  const filtered=voices.filter(v=>{
    const mf=voiceFilter==='all'||v.category===voiceFilter||(voiceFilter==='male'&&v.gender==='male')||(voiceFilter==='female'&&v.gender==='female');
    const mq=!q||v.name.toLowerCase().includes(q)||(v.accent||'').toLowerCase().includes(q);
    return mf&&mq;
  });
  grid.innerHTML=filtered.length?filtered.map(v=>voiceCardHTML(v)).join(''):'<div style="grid-column:1/-1;text-align:center;padding:30px;color:var(--tx3)">검색 결과 없음</div>';
}
function selectVoice(vid){
  const v=voices.find(x=>x.voice_id===vid);if(!v)return;
  selectedVoice[voiceTarget]=v;
  // 최근 사용 업데이트 (최신이 맨 앞, 최대 10개)
  recentVoiceIds=recentVoiceIds.filter(id=>id!==vid);
  recentVoiceIds.unshift(vid);
  if(recentVoiceIds.length>10)recentVoiceIds=recentVoiceIds.slice(0,10);
  try{localStorage.setItem('hwak_recent_voices',JSON.stringify(recentVoiceIds));}catch(e){}
  const col=avatarColor(v.name),ini=initials(v.name).toUpperCase();
  const pfx=voiceTarget;
  const av=$(pfx+'-vav');av.textContent=ini;av.style.background=col;
  $(pfx+'-vname').textContent=v.name;
  $(pfx+'-vmeta').textContent=[v.gender,v.accent,v.use_case].filter(Boolean).join(' · ')||'목소리';
  $(pfx+'-voice-btn').classList.add('selected');
  closeVoiceModal();notify(v.name+' 선택됨','ok');
}
function playPrev(url,e){if(e)e.stopPropagation();if(currentAudio)currentAudio.pause();currentAudio=new Audio(url);currentAudio.play().catch(()=>notify('미리듣기 실패','err'));}

// STYLES
async function loadStyles(){
  const r=await fetch('/api/img/styles');const d=await r.json();
  const st=d.styles||[];styles={};st.forEach(s=>styles[s.key]=s);
  ['img-style-grid','combo-style-grid'].forEach((gridId,gi)=>{
    const grid=$(gridId);
    grid.innerHTML=st.map((s,i)=>`<div class="style-card${i===0?' active':''}" onclick="selectStyle('${s.key.replace(/'/g,"\\'")}','${gi===0?'img':'combo'}',this)"><div class="style-card-name">${s.key}</div><div class="style-card-ratio">${s.ratio}${s.custom?' · 커스텀':''}</div></div>`).join('');
  });
  if(st.length>0){
    imgStyle=st[0].key; comboStyle=st[0].key;
    // 크립토툰을 콤보 기본값으로 (있으면)
    const cryptoKey=st.find(s=>s.key.includes('크립토'));
    if(cryptoKey){ comboStyle=cryptoKey.key;
      document.querySelectorAll('#combo-style-grid .style-card').forEach(c=>c.classList.remove('active'));
      const idx=st.indexOf(cryptoKey);
      const cards=document.querySelectorAll('#combo-style-grid .style-card');
      if(cards[idx]) cards[idx].classList.add('active');
    }
  }
}
function selectStyle(key,ns,el){
  if(ns==='img')imgStyle=key;else comboStyle=key;
  const gridId=ns+'-style-grid';
  document.querySelectorAll('#'+gridId+' .style-card').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');
  const isCustom=styles[key]?.custom;
  $(ns==='img'?'custom-prompt-sec':'combo-custom-sec').style.display=isCustom?'block':'none';
}

// IMAGE
async function imgGenerate(){
  const script=$('img-script').value.trim();if(!script){notify('대본 입력','err');return;}
  startSSE('img-log','img-prog','img-prog-fill','img-prog-lbl','img-prog-pct','img-cancel-btn','img-gen-btn');
  const r=await fetch('/api/img/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    tab_id:TAB_ID,script,style_key:imgStyle,custom_prompt:$('img-custom-prompt').value,
    ref_image_path:refPaths.img,out_dir:$('img-outdir').value||'studio_output',project:$('img-project').value.trim(),
  })});
  const d=await r.json();if(d.error){notify(d.error,'err');finishSSE($('img-cancel-btn'),$('img-gen-btn'));}
}

// COMBO
async function comboPreview(){
  if(!selectedVoice.combo){notify('목소리를 먼저 선택하세요','err');return;}
  const script=$('combo-script').value.trim(); if(!script){notify('대본 입력','err');return;}
  const scenes=script.split(/(?:^|\r?\n)\s*-{3,}[-<\s]*(?:\r?\n|$)/m).map(s=>s.trim()).filter(Boolean);
  const text=(scenes[0]||script).slice(0,500);
  notify('미리듣기 생성 중...');
  try{
    const r=await fetch('/api/tts/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      text, voice_id:selectedVoice.combo.voice_id, model_id:comboModel,
      stability:parseFloat($('csl-stab').value), similarity_boost:parseFloat($('csl-sim').value),
      style:0, use_speaker_boost:$('ctg-spkbst').checked,
      speed:parseFloat($('csl-spd').value), output_format:$('combo-fmt').value,
    })});
    if(!r.ok){const d=await r.json();notify(d.error||'실패','err');return;}
    const blob=await r.blob();
    if(currentAudio)currentAudio.pause();
    currentAudio=new Audio(URL.createObjectURL(blob));currentAudio.play();
    notify('재생 중...','ok');
  }catch(e){notify('미리듣기 실패: '+e,'err');}
}
async function comboGenerate(){
  const script=$('combo-script').value.trim();if(!script){notify('대본 입력','err');return;}
  if(!selectedVoice.combo){notify('목소리를 선택하세요','err');return;}
  startSSE('combo-log','combo-prog','combo-prog-fill','combo-prog-lbl','combo-prog-pct','combo-cancel-btn','combo-gen-btn');
  const r=await fetch('/api/combo/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    tab_id:TAB_ID,script,voice_id:selectedVoice.combo.voice_id,model_id:comboModel,
    stability:parseFloat($('csl-stab').value),similarity_boost:parseFloat($('csl-sim').value),
    style:0,use_speaker_boost:$('ctg-spkbst').checked,speed:parseFloat($('csl-spd').value),
    output_format:$('combo-fmt').value,enhance:$('ctg-enhance').checked,
    style_key:comboStyle,custom_prompt:$('combo-custom-prompt').value,
    ref_image_path:refPaths.combo,
    prefix:$('combo-prefix').value.trim()||'scene',out_dir:$('combo-outdir').value||'studio_output',
    project:$('combo-project').value.trim(),
  })});
  const d=await r.json();if(d.error){notify(d.error,'err');finishSSE($('combo-cancel-btn'),$('combo-gen-btn'));}
}
</script>
</body>
</html>"""


# ── 실행 ──────────────────────────────────────────────
if __name__ == "__main__":
    ensure_dir(DEFAULT_OUT)
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n{'='*55}")
    print(f"  🎬  황작가 AI 스튜디오 v2.0")
    print(f"  {url}")
    print(f"{'='*55}")
    print(f"  TTS | 이미지생성(13화풍) | 이미지+음성 동시 | 설정")
    print(f"  종료: Ctrl+C")
    print()
    def open_b():
        time.sleep(1.0)
        webbrowser.open(url)
    threading.Thread(target=open_b, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
