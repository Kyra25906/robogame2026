from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .batch_report import (
    EXPECTED_VALUES,
    MANIFEST_SCHEMA_VERSION,
    evaluate_manifest,
    format_html,
    load_manifest,
)
from .report import load_records, timeline_metadata


DEFAULTS = {
    "min_detection_ratio": 0.9,
    "max_unexpected_detection_ratio": 0.0,
}


def resolve_detector_config(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.resolve()
        if not candidate.is_file():
            raise ValueError(f"detector configuration does not exist: {candidate}")
        return candidate
    source_config = Path(__file__).resolve().parent.parent / "config" / "vision_default.json"
    if source_config.is_file():
        return source_config
    try:
        from ament_index_python.packages import get_package_share_directory

        installed_config = (
            Path(get_package_share_directory("cube_perception"))
            / "config" / "vision_default.json"
        )
    except (ImportError, LookupError):
        installed_config = Path()
    if installed_config.is_file():
        return installed_config
    raise ValueError(
        "cannot find vision_default.json; pass its path with --config"
    )


def relative_path(target: Path, base: Path) -> str:
    try:
        return target.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        import os

        return Path(os.path.relpath(target.resolve(), base.resolve())).as_posix()


def validate_segments(raw_segments: object) -> list[dict]:
    if not isinstance(raw_segments, list):
        raise ValueError("segments must be a list")
    segments = []
    for index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"segment {index} must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"segment {index} name cannot be empty")
        expected = raw.get("expected")
        if expected not in EXPECTED_VALUES:
            raise ValueError(
                f"segment {index} expected must be one of: {', '.join(EXPECTED_VALUES)}"
            )
        try:
            start_s = float(raw["start_s"])
            end_s = float(raw["end_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"segment {index} requires numeric start_s and end_s") from exc
        if start_s < 0.0 or end_s < 0.0:
            raise ValueError(f"segment {index} times cannot be negative")
        if end_s <= start_s:
            raise ValueError(f"segment {index} end_s must be later than start_s")
        segments.append(
            {
                "name": name.strip(),
                "start_s": round(start_s, 3),
                "end_s": round(end_s, 3),
                "expected": expected,
            }
        )
    if not segments:
        raise ValueError("at least one segment is required before saving")
    return segments


def load_editor_state(
    *,
    manifest_path: Path,
    video_path: Path | None,
    jsonl_path: Path | None,
    dataset_name: str,
) -> dict:
    manifest = None
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
    jsonl_value = (
        relative_path(jsonl_path, manifest_path.parent) if jsonl_path else ""
    )
    video_value = relative_path(video_path, manifest_path.parent) if video_path else ""
    matched = None
    if manifest is not None and jsonl_path is not None:
        matched = next(
            (
                dataset
                for dataset in manifest["datasets"]
                if dataset.get("jsonl") == jsonl_value
            ),
            None,
        )
    return {
        "manifest_name": (
            manifest["name"] if manifest is not None else "Vision acceptance"
        ),
        "dataset_name": matched.get("name", dataset_name) if matched else dataset_name,
        "jsonl": jsonl_value,
        "video": video_value,
        "segments": matched.get("segments", []) if matched else [],
        "video_ready": video_path is not None,
        "jsonl_ready": jsonl_path is not None,
    }


def unique_upload_path(folder: Path, filename: str) -> Path:
    cleaned = Path(filename.replace("\\", "/")).name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("uploaded filename is invalid")
    candidate = folder / cleaned
    counter = 2
    while candidate.exists():
        candidate = folder / f"{Path(cleaned).stem}_{counter}{Path(cleaned).suffix}"
        counter += 1
    return candidate


def save_editor_state(
    *,
    manifest_path: Path,
    video_path: Path,
    jsonl_path: Path,
    payload: object,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("save payload must be an object")
    manifest_name = payload.get("manifest_name")
    dataset_name = payload.get("dataset_name")
    if not isinstance(manifest_name, str) or not manifest_name.strip():
        raise ValueError("manifest name cannot be empty")
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        raise ValueError("dataset name cannot be empty")
    segments = validate_segments(payload.get("segments"))
    jsonl_value = relative_path(jsonl_path, manifest_path.parent)
    dataset = {
        "name": dataset_name.strip(),
        "video": relative_path(video_path, manifest_path.parent),
        "jsonl": jsonl_value,
        "segments": segments,
    }

    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        datasets = list(manifest["datasets"])
        match_index = next(
            (
                index
                for index, item in enumerate(datasets)
                if item.get("jsonl") == jsonl_value
            ),
            None,
        )
        if match_index is None:
            datasets.append(dataset)
        else:
            datasets[match_index] = dataset
        manifest["name"] = manifest_name.strip()
        manifest["datasets"] = datasets
    else:
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "name": manifest_name.strip(),
            "defaults": dict(DEFAULTS),
            "datasets": [dataset],
        }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def render_editor_page(state: dict) -> str:
    encoded = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    video_source = "/video" if state.get("video_ready") else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>视觉时间段标注</title>
  <style>
    :root {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; color: #172033; }}
    body {{ max-width: 1200px; margin: auto; padding: 24px; background: #f4f6f8; }}
    h1 {{ margin-top: 0; }} .panel {{ padding: 18px; margin: 14px 0; border-radius: 10px; background: white; }}
    video {{ width: 100%; max-height: 58vh; background: #111827; border-radius: 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }}
    label {{ display: block; color: #4b5563; font-size: .9rem; }}
    input, select, button {{ box-sizing: border-box; width: 100%; margin-top: 5px; padding: 9px; font: inherit; }}
    button {{ border: 0; border-radius: 7px; color: white; background: #2563eb; cursor: pointer; }}
    button.secondary {{ background: #475569; }} button.danger {{ background: #b91c1c; }}
    table {{ width: 100%; border-collapse: collapse; }} th, td {{ padding: 9px; border: 1px solid #d1d5db; text-align: left; }}
    th {{ background: #e5e7eb; }} .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    #status {{ min-height: 1.5em; font-weight: 600; }} .ok {{ color: #15803d; }} .error {{ color: #b91c1c; }}
    .hint {{ color: #6b7280; }} code {{ background: #eef2ff; padding: 2px 5px; }}
  </style>
</head>
<body>
  <h1>视觉时间段标注菜单</h1>
  <p class="hint">文件只提交到本机 <code>127.0.0.1</code> 服务，不会上传互联网。优先选择浏览器兼容的原始 MP4；JSONL 必须来自该视频的同一次检测。</p>
  <section class="panel grid">
    <label>选择视频<input id="videoFile" type="file" accept="video/*,.mp4,.mov,.webm,.mkv,.avi"></label>
    <label>选择对应 JSONL<input id="jsonlFile" type="file" accept=".jsonl,application/x-ndjson"></label>
    <div><button id="uploadFiles">提交所选文件到本地工作目录</button></div>
    <div><button id="processVideo" class="secondary">处理新视频并生成检测时间线</button></div>
    <div id="fileState" class="hint"></div>
  </section>
  <section class="panel"><video id="video" src="{video_source}" controls preload="metadata"></video></section>
  <section class="panel grid">
    <label>验收任务名称<input id="manifestName"></label>
    <label>数据集名称<input id="datasetName"></label>
    <label>时间段名称<input id="segmentName" placeholder="例如：橙色方块清晰可见"></label>
    <label>期望结果<select id="expected"><option value="orange">orange</option><option value="purple">purple</option><option value="absent">absent</option></select></label>
    <label>开始时间（秒）<input id="start" type="number" min="0" step="0.001"></label>
    <label>结束时间（秒）<input id="end" type="number" min="0" step="0.001"></label>
  </section>
  <section class="panel actions">
    <button id="setStart" class="secondary">把当前播放位置设为开始 [</button>
    <button id="setEnd" class="secondary">把当前播放位置设为结束 ]</button>
    <button id="add">添加时间段</button>
    <button id="save">保存 manifest</button>
    <button id="report">生成 HTML 验收报告</button>
    <div id="reportLink"></div>
  </section>
  <section class="panel">
    <h2>已标注时间段</h2>
    <div style="overflow-x:auto"><table><thead><tr><th>名称</th><th>开始</th><th>结束</th><th>期望</th><th>操作</th></tr></thead><tbody id="segments"></tbody></table></div>
  </section>
  <p id="status"></p>
  <script>
    const initial = {encoded};
    const video = document.getElementById('video');
    const segments = [...initial.segments];
    let videoReady = initial.video_ready;
    let jsonlReady = initial.jsonl_ready;
    const byId = id => document.getElementById(id);
    byId('manifestName').value = initial.manifest_name;
    byId('datasetName').value = initial.dataset_name;
    const time = () => Number(video.currentTime.toFixed(3));
    const show = (message, error=false) => {{ const el=byId('status'); el.textContent=message; el.className=error?'error':'ok'; }};
    const showFiles = () => {{ byId('fileState').textContent = `视频：${{videoReady?'已就绪':'未选择'}}；JSONL：${{jsonlReady?'已就绪':'未选择'}}`; }};
    function render() {{
      byId('segments').innerHTML = '';
      segments.forEach((item, index) => {{
        const row = document.createElement('tr');
        [item.name, item.start_s.toFixed(3), item.end_s.toFixed(3), item.expected].forEach(value => {{ const cell=document.createElement('td'); cell.textContent=value; row.appendChild(cell); }});
        const action = document.createElement('td'); const remove = document.createElement('button'); remove.textContent='删除'; remove.className='danger'; remove.onclick=()=>{{segments.splice(index,1);render();}}; action.appendChild(remove); row.appendChild(action); byId('segments').appendChild(row);
      }});
    }}
    byId('setStart').onclick = () => {{ byId('start').value = time().toFixed(3); }};
    byId('setEnd').onclick = () => {{ byId('end').value = time().toFixed(3); }};
    document.addEventListener('keydown', event => {{ if (['INPUT','SELECT'].includes(document.activeElement.tagName)) return; if(event.key==='[') byId('setStart').click(); if(event.key===']') byId('setEnd').click(); }});
    byId('add').onclick = () => {{
      const item={{name:byId('segmentName').value.trim(),start_s:Number(byId('start').value),end_s:Number(byId('end').value),expected:byId('expected').value}};
      if(!item.name) return show('请填写时间段名称。',true);
      if(!Number.isFinite(item.start_s)||!Number.isFinite(item.end_s)||item.end_s<=item.start_s) return show('结束时间必须晚于开始时间。',true);
      segments.push(item); segments.sort((a,b)=>a.start_s-b.start_s); byId('segmentName').value=''; render(); show('时间段已加入列表，记得点击保存。');
    }};
    async function uploadFile(kind, file) {{
      const response=await fetch(`/upload/${{kind}}`,{{method:'POST',headers:{{'X-Filename':encodeURIComponent(file.name),'Content-Type':'application/octet-stream'}},body:file}});
      const result=await response.json(); if(!response.ok) throw new Error(result.error||'文件提交失败'); return result;
    }}
    byId('videoFile').onchange = () => {{ const file=byId('videoFile').files[0]; if(file) video.src=URL.createObjectURL(file); }};
    byId('uploadFiles').onclick = async () => {{
      const videoFile=byId('videoFile').files[0]; const jsonlFile=byId('jsonlFile').files[0];
      if(!videoFile&&!jsonlFile) return show('请至少选择一个需要提交的文件。',true);
      try {{
        if(videoFile) {{ await uploadFile('video',videoFile); videoReady=true; video.src=`/video?t=${{Date.now()}}`; }}
        if(jsonlFile) {{ await uploadFile('jsonl',jsonlFile); jsonlReady=true; }}
        showFiles(); show('文件已经提交到本地工作目录，可以开始标注。');
      }} catch(error) {{ show(error.message,true); }}
    }};
    byId('processVideo').onclick = async () => {{
      if(!videoReady) return show('请先选择并提交新视频。',true);
      try {{
        show('正在逐帧处理视频，请保持此页面打开……');
        const response=await fetch('/process',{{method:'POST'}}); const result=await response.json();
        if(!response.ok) throw new Error(result.error||'视频处理失败');
        jsonlReady=true; showFiles();
        show(`检测完成：${{result.record_count}} 帧，时间线已自动关联。`);
      }} catch(error) {{ show(error.message,true); }}
    }};
    byId('save').onclick = async () => {{
      try {{
        const response=await fetch('/save',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{manifest_name:byId('manifestName').value,dataset_name:byId('datasetName').value,segments}})}});
        const result=await response.json(); if(!response.ok) throw new Error(result.error||'保存失败'); show(`已保存 ${{result.segment_count}} 个时间段：${{result.manifest}}`);
      }} catch(error) {{ show(error.message,true); }}
    }};
    byId('report').onclick = async () => {{
      try {{
        const response=await fetch('/report',{{method:'POST'}}); const result=await response.json();
        if(!response.ok) throw new Error(result.error||'报告生成失败');
        const link=document.createElement('a'); link.href=result.url; link.target='_blank';
        link.textContent=`打开 HTML 报告（${{result.passed_segments}}/${{result.segment_count}} 通过）`;
        byId('reportLink').replaceChildren(link); show(`HTML 报告已生成：${{result.report}}`);
      }} catch(error) {{ show(error.message,true); }}
    }};
    render(); showFiles();
  </script>
</body>
</html>
"""


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        raise ValueError("unsupported byte range")
    first, last = match.groups()
    if not first and not last:
        raise ValueError("empty byte range")
    if first:
        start = int(first)
        end = int(last) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("invalid suffix byte range")
        start = max(0, size - suffix)
        end = size - 1
    if start >= size or end < start:
        raise ValueError("byte range is outside video")
    return start, min(end, size - 1)


def make_handler(
    *,
    manifest_path: Path,
    video_path: Path | None,
    jsonl_path: Path | None,
    initial_state: dict,
    detector_config: Path | None = None,
    process_video_callback=None,
):
    page = render_editor_page(initial_state).encode("utf-8")
    files = {"video": video_path, "jsonl": jsonl_path}
    upload_folder = manifest_path.parent / "uploads"
    report_path = manifest_path.parent / "vision_batch_report.html"

    def process_video(source: Path, output: Path) -> None:
        if process_video_callback is not None:
            process_video_callback(source, output)
            return
        if detector_config is None or not detector_config.is_file():
            raise ValueError("detector configuration is missing; use --config")
        try:
            from .standalone import main as run_detector
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                raise ValueError(
                    "OpenCV (cv2) is unavailable in the current Python: "
                    f"{sys.executable}. Install it with: "
                    f'\"{sys.executable}\" -m pip install opencv-python'
                ) from exc
            raise
        result = run_detector([
            "--source", str(source),
            "--config", str(detector_config),
            "--jsonl", str(output),
            "--headless",
        ])
        if result != 0:
            raise ValueError(f"video detector exited with code {result}")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if route == "/video":
                self._serve_video()
                return
            if route == "/report.html" and report_path.is_file():
                body = report_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _serve_video(self):
            current_video = files["video"]
            if current_video is None:
                self.send_error(HTTPStatus.NOT_FOUND, "no video has been submitted")
                return
            size = current_video.stat().st_size
            try:
                selected = parse_byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            start, end = selected if selected else (0, size - 1)
            self.send_response(
                HTTPStatus.PARTIAL_CONTENT if selected else HTTPStatus.OK
            )
            self.send_header(
                "Content-Type", mimetypes.guess_type(current_video.name)[0] or "video/mp4"
            )
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if selected:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with current_video.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_POST(self):
            route = urlparse(self.path).path
            if route in {"/upload/video", "/upload/jsonl"}:
                self._receive_upload(route.rsplit("/", 1)[-1])
                return
            if route == "/process":
                self._process_video()
                return
            if route == "/report":
                self._generate_report()
                return
            if route != "/save":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                if files["video"] is None:
                    raise ValueError("select and submit a video before saving")
                if files["jsonl"] is None:
                    raise ValueError("select and submit a matching schema v2 JSONL before saving")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2_000_000:
                    raise ValueError("invalid save request size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                manifest = save_editor_state(
                    manifest_path=manifest_path,
                    video_path=files["video"],
                    jsonl_path=files["jsonl"],
                    payload=payload,
                )
                segment_count = next(
                    len(item["segments"])
                    for item in manifest["datasets"]
                    if item["jsonl"]
                    == relative_path(files["jsonl"], manifest_path.parent)
                )
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(
                HTTPStatus.OK,
                {"manifest": str(manifest_path), "segment_count": segment_count},
            )

        def _process_video(self) -> None:
            try:
                source = files["video"]
                if source is None:
                    raise ValueError("select and submit a video before processing")
                upload_folder.mkdir(parents=True, exist_ok=True)
                output = unique_upload_path(
                    upload_folder, f"{source.stem}_detections.jsonl"
                )
                process_video(source, output)
                records = load_records(output)
                timeline_metadata(records)
                files["jsonl"] = output
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                if 'output' in locals() and output.is_file():
                    output.unlink()
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {
                "jsonl": str(output),
                "record_count": len(records),
                "duration_s": float(records[-1]["timestamp_s"]),
            })

        def _generate_report(self) -> None:
            try:
                if not manifest_path.is_file():
                    raise ValueError("save the manifest before generating a report")
                manifest = load_manifest(manifest_path)
                result = evaluate_manifest(manifest_path, manifest)
                report_path.write_text(format_html(result), encoding="utf-8")
            except (OSError, TypeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {
                "report": str(report_path),
                "url": "/report.html",
                "passed": result["passed"],
                "passed_segments": result["passed_segments"],
                "segment_count": result["segment_count"],
            })

        def _receive_upload(self, kind: str) -> None:
            target = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4_000_000_000:
                    raise ValueError("uploaded file must be between 1 byte and 4 GB")
                filename = unquote(self.headers.get("X-Filename", ""))
                suffix = Path(filename).suffix.lower()
                allowed = (
                    {".mp4", ".mov", ".webm", ".mkv", ".avi"}
                    if kind == "video"
                    else {".jsonl"}
                )
                if suffix not in allowed:
                    raise ValueError(
                        f"unsupported {kind} extension {suffix or '(none)'}"
                    )
                upload_folder.mkdir(parents=True, exist_ok=True)
                target = unique_upload_path(upload_folder, filename)
                remaining = length
                with target.open("wb") as handle:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("upload ended before the declared file size")
                        handle.write(chunk)
                        remaining -= len(chunk)
                if kind == "jsonl":
                    records = load_records(target)
                    timeline_metadata(records)
                files[kind] = target
            except (OSError, TypeError, ValueError) as exc:
                if target is not None and target.is_file():
                    target.unlink()
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(
                HTTPStatus.OK,
                {"kind": kind, "filename": target.name, "path": str(target)},
            )

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a local browser menu for annotating video time segments"
    )
    parser.add_argument("--video", type=Path, help="optional video to preload")
    parser.add_argument("--jsonl", type=Path, help="optional matching schema v2 JSONL")
    parser.add_argument("--workspace", type=Path, default=Path("results/vision_annotations"))
    parser.add_argument("--manifest", type=Path, help="default: WORKSPACE/vision_acceptance.json")
    parser.add_argument("--dataset-name", default="Annotated video")
    parser.add_argument(
        "--config", type=Path,
        default=None,
        help="detector configuration used by the web processing button",
    )
    parser.add_argument("--port", type=int, default=0, help="local port; 0 chooses a free port")
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    video = args.video.resolve() if args.video else None
    jsonl_path = args.jsonl.resolve() if args.jsonl else None
    manifest = (
        args.manifest.resolve()
        if args.manifest
        else workspace / "vision_acceptance.json"
    )
    if video is not None and not video.is_file():
        parser.error(f"video does not exist: {video}")
    if jsonl_path is not None and not jsonl_path.is_file():
        parser.error(f"JSONL does not exist: {jsonl_path}")
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        detector_config = resolve_detector_config(args.config)
        if jsonl_path is not None:
            timeline_metadata(load_records(jsonl_path))
        state = load_editor_state(
            manifest_path=manifest,
            video_path=video,
            jsonl_path=jsonl_path,
            dataset_name=args.dataset_name,
        )
    except ValueError as exc:
        parser.error(str(exc))
    handler = make_handler(
        manifest_path=manifest,
        video_path=video,
        jsonl_path=jsonl_path,
        initial_state=state,
        detector_config=detector_config,
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"annotation menu: {url}")
    print("press Ctrl+C in this terminal when annotation is finished")
    if not args.no_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("annotation menu stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
