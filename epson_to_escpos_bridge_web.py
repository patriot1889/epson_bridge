#!/usr/bin/env python3
"""
Epson TM-m30 to ESC/POS Bridge with Web Interface

- Emulates an Epson TM-m30 printer (receives ESC/POS data)
- Converts ESC/POS data to PNG
- Forwards the data to a real ESC/POS printer
- Runs both the emulator and printer connection continuously
- Serves a web interface for reprinting jobs

Requirements:
- python-escpos
- Pillow

Usage:
    python3 epson_to_escpos_bridge.py --printer-ip 192.168.1.100
"""
import os
import threading
import time
import queue
import argparse
import socket
import logging
from datetime import datetime
from escpos.printer import Network, Usb
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import urllib.parse
import concurrent.futures
import uuid
from collections import deque

# Set up logging
def setup_logging():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"epson_bridge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    
    # Remove any existing handlers to avoid duplicates
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    # Create formatters and handlers
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Configure root logger
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(file_handler)
    logging.root.addHandler(console_handler)
    
    # Log startup message
    logging.info(f"Logging initialized. Log file: {log_file}")

setup_logging()

# Import Epson emulator and graphics converter logic
from mix import epson_emulator
from mix import escpos_graphics_converter


class WebInterface(BaseHTTPRequestHandler):
    def __init__(self, bridge_instance, *args, **kwargs):
        self.bridge = bridge_instance
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Override to add custom logging"""
        if self.path != '/api/status':  # Skip logging for status endpoint
            logging.info(f"[WebServer] {format % args}")

    def do_GET(self):
        """Handle GET requests"""
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == '/':
                self.serve_main_page()
            elif path == '/api/status':
                self.serve_status()
            elif path == '/api/stream':
                self.serve_sse()
            elif path.startswith('/static/'):
                self.serve_static_file()
            elif path.startswith('/print_jobs/') and path.endswith('.png'):
                self.serve_image()
            else:
                self.send_error(404)
        except Exception as e:
            logging.error(f"[WebServer] GET error for {self.path}: {e}")
            self.send_error(500)
            
    def do_OPTIONS(self):
        """Handle OPTIONS requests for CORS preflight"""
        try:
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Content-Length', '0')
            self.end_headers()
        except Exception as e:
            print(f"[WebServer] OPTIONS error: {e}")
            self.send_error(500)

    def do_POST(self):
        """Handle POST requests"""
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == '/api/reprint':
                self.handle_reprint()
            elif path == '/api/restart_printer':
                self.handle_restart_printer()
            else:
                self.send_error(404)
        except Exception as e:
            print(f"[WebServer] POST error for {self.path}: {e}")
            self.send_error(500)

    def serve_main_page(self):
        """Serve the main HTML page"""
        html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Epson Printer Bridge</title>
    <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
    <link rel="apple-touch-icon" href="/static/favicon.ico">
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }
        .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #333; text-align: center; margin-bottom: 30px; }
        .status { background: #e8f5e8; padding: 15px; margin-bottom: 20px; border-left: 4px solid #4caf50; }
    /* Section wrapper for grouped content (keeps the left accent) */
    .jobs-section { background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 6px solid #2196f3; }
    /* Individual job cards (no nested left border) */
    .print-job { background: #fff; padding: 16px; border-radius: 6px; margin-bottom: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border-left: none; }
        .button { background-color: #4caf50; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; margin: 5px; transition: background-color 0.3s; }
        .button:hover { background-color: #45a049; }
        .button:disabled { background-color: #cccccc; cursor: not-allowed; }
        .button.reprint { background-color: #2196f3; }
        .button.reprint:hover { background-color: #1976d2; }
        .message { padding: 10px; margin: 10px 0; border-radius: 5px; display: none; }
        .success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .preview-image { max-width: 100%; border: 1px solid #ddd; border-radius: 5px; margin-top: 10px; }
        .loading { display: inline-block; width: 20px; height: 20px; border: 3px solid #f3f3f3; border-top: 3px solid #3498db; border-radius: 50%; animation: spin 1s linear infinite; margin-right: 10px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .uptime { color: #666; font-size: 0.9em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Epson Printer Bridge Control</h1>

        <!-- Last Print Job removed: use Recent Jobs list for reprints -->
        
        <div class="jobs-section">
            <h3>Recent Jobs (last 24h)</h3>
            <div id="recent-jobs-list">Loading...</div>
            <div id="jobs-pagination" style="display:flex; align-items:center; gap:8px; margin-top:8px;">
                <button id="jobs-prev" class="button" onclick="jobsPrev()" disabled>Prev</button>
                <span id="jobs-page-indicator">Page 1</span>
                <button id="jobs-next" class="button" onclick="jobsNext()" disabled>Next</button>
            </div>
        </div>
        
        <div class="status">
            <h3>Status</h3>
            <p><strong>Boot Time:</strong> <span id="boot-time">Loading...</span></p>
            <p><strong>Uptime:</strong> <span id="uptime" class="uptime">Loading...</span></p>
            <p><strong>Emulator IP:</strong> <span id="emulator-ip">Loading...</span></p>
            <p><strong>Printer:</strong> <span id="printer-info">Loading...</span></p>
            <p><strong>Jobs Processed:</strong> <span id="jobs-count">0</span></p>
            <div style="display:flex; gap:8px; align-items:center;">
                <button class="button" onclick="refreshStatus()">Refresh Status</button>
                <button id="restart-printer-btn" class="button" style="background-color:#ff9800;color:#fff" onclick="restartPrinter()">
                    <span id="restart-loading" class="loading" style="display:none"></span>
                    Restart Printer Connection
                </button>
            </div>
        </div>

        <div id="message" class="message"></div>
    </div>

    <script>
    // lastJobData removed; recent jobs list provides reprint controls

        // Pagination state
    const JOBS_PER_PAGE = 5;
        let _jobs_all = [];
        let _jobs_page = 0;

        function renderRecentJobs(recent) {
            _jobs_all = Array.isArray(recent) ? recent : [];
            _jobs_page = 0;
            _renderJobsPage();
            _updatePaginationControls();
        }

        function _renderJobsPage() {
            const container = document.getElementById('recent-jobs-list');
            container.innerHTML = '';
            if (!_jobs_all || _jobs_all.length === 0) {
                container.textContent = 'No recent jobs';
                return;
            }

            const start = _jobs_page * JOBS_PER_PAGE;
            const end = Math.min(start + JOBS_PER_PAGE, _jobs_all.length);
            for (let i = start; i < end; i++) {
                const job = _jobs_all[i];
                const div = document.createElement('div');
                div.className = 'print-job';
                const time = new Date(job.timestamp * 1000).toLocaleString();
                let html = `<strong>Timestamp:</strong> ${time}<br><strong>Size:</strong> ${job.size} bytes`;
                if (job.preview_path) {
                    html += `<div><img src="${job.preview_path}" class="preview-image" alt="preview" style="max-width:200px;display:block;margin-top:8px"></div>`;
                }
                html += `<div style="margin-top:8px"><button class="button reprint" onclick="reprintJob('${job.id}', this)"><span class=\"loading\" style=\"display:none\"></span>Reprint</button></div>`;
                div.innerHTML = html;
                container.appendChild(div);
            }
            document.getElementById('jobs-page-indicator').textContent = `Page ${_jobs_page + 1} of ${Math.max(1, Math.ceil(_jobs_all.length / JOBS_PER_PAGE))}`;
        }

        function _updatePaginationControls() {
            const prev = document.getElementById('jobs-prev');
            const next = document.getElementById('jobs-next');
            const totalPages = Math.ceil(_jobs_all.length / JOBS_PER_PAGE);
            if (prev) prev.disabled = _jobs_page <= 0;
            if (next) next.disabled = _jobs_page >= (totalPages - 1) || totalPages <= 1;
        }

        function jobsNext() {
            const totalPages = Math.ceil(_jobs_all.length / JOBS_PER_PAGE);
            if (_jobs_page < totalPages - 1) {
                _jobs_page++;
                _renderJobsPage();
                _updatePaginationControls();
            }
        }

        function jobsPrev() {
            if (_jobs_page > 0) {
                _jobs_page--;
                _renderJobsPage();
                _updatePaginationControls();
            }
        }

        function formatUptime(seconds) {
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);
            
            let result = '';
            if (days > 0) result += `${days}d `;
            if (hours > 0) result += `${hours}h `;
            if (minutes > 0) result += `${minutes}m `;
            result += `${secs}s`;
            
            return result;
        }

        function showMessage(text, type) {
            const messageDiv = document.getElementById('message');
            messageDiv.textContent = text;
            messageDiv.className = `message ${type}`;
            messageDiv.style.display = 'block';
            setTimeout(() => { messageDiv.style.display = 'none'; }, 5000);
        }

        function refreshStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('emulator-ip').textContent = data.emulator_ip || 'Not available';
                    document.getElementById('printer-info').textContent = data.printer_info || 'Not connected';
                    document.getElementById('jobs-count').textContent = data.jobs_processed || 0;
                    
                    // Update boot time and uptime
                    if (data.boot_time) {
                        document.getElementById('boot-time').textContent = new Date(data.boot_time * 1000).toLocaleString();
                    }
                    if (data.uptime) {
                        document.getElementById('uptime').textContent = formatUptime(data.uptime);
                    }
                    
                                // last_job is ignored here because recent_jobs contains the full list

                    // Render recent jobs list
                    if (Array.isArray(data.recent_jobs)) {
                        renderRecentJobs(data.recent_jobs);
                    }
                })
                .catch(error => { console.error('Error fetching status:', error); showMessage('Error fetching status', 'error'); });
        }

    // reprintLastJob removed; recent jobs list contains reprint buttons

        function restartPrinter() {
            const btn = document.getElementById('restart-printer-btn');
            const loading = document.getElementById('restart-loading');
            if (loading) loading.style.display = 'inline-block';
            btn.disabled = true;
            return fetch('/api/restart_printer', { method: 'POST' })
                .then(resp => { if (!resp.ok) throw new Error(`HTTP ${resp.status}`); return resp.json(); })
                .then(data => {
                    if (data.success) {
                        showMessage(data.message || 'Printer restart requested', 'success');
                    } else {
                        showMessage(data.message || 'Printer restart failed', 'error');
                    }
                })
                .catch(err => { console.error('Restart error:', err); showMessage('Error requesting restart', 'error'); })
                .finally(() => { if (loading) loading.style.display = 'none'; btn.disabled = false; });
        }

        function reprintJob(jobId, btn) {
            let loading = null;
            if (btn) { loading = btn.querySelector('.loading'); if (loading) loading.style.display = 'inline-block'; btn.disabled = true; } else { loading = document.getElementById('reprint-loading'); if (loading) loading.style.display = 'inline-block'; }
            const body = jobId ? JSON.stringify({job_id: jobId}) : JSON.stringify({});
            return fetch('/api/reprint', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body })
            .then(response => { if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`); return response.json(); })
            .then(data => { if (data.success) { showMessage(data.message || 'Print job queued successfully!', 'success'); } else { showMessage(data.message || 'Error reprinting job', 'error'); } })
            .catch(err => { console.error('Reprint error:', err); showMessage('Error sending reprint request', 'error'); })
            .finally(() => { if (btn) { if (loading) loading.style.display = 'none'; btn.disabled = false; } else { if (loading) loading.style.display = 'none'; const defaultBtn = document.getElementById('reprint-btn'); if (defaultBtn) defaultBtn.disabled = false; } });
        }

        // SSE with exponential backoff reconnect; no continuous polling
        const _sse_supported = typeof(EventSource) !== 'undefined';
        let _es = null;
        let _sse_backoff = 1000; // 1s
        const _sse_backoff_max = 30000; // 30s

        function setupSSE() {
            try {
                _es = new EventSource('/api/stream');

                _es.addEventListener('snapshot', e => {
                    const data = JSON.parse(e.data);
                    if (data.recent_jobs) renderRecentJobs(data.recent_jobs);
                    if (data.boot_time) document.getElementById('boot-time').textContent = new Date(data.boot_time * 1000).toLocaleString();
                    if (data.uptime) document.getElementById('uptime').textContent = formatUptime(data.uptime);
                    document.getElementById('emulator-ip').textContent = data.emulator_ip || 'Not available';
                    document.getElementById('printer-info').textContent = data.printer_info || 'Not connected';
                    document.getElementById('jobs-count').textContent = data.jobs_processed || 0;
                });

                _es.addEventListener('new_job', e => {
                    const payload = JSON.parse(e.data);
                    _jobs_all.unshift(payload);
                    _renderJobsPage();
                    _updatePaginationControls();
                });

                _es.addEventListener('job_updated', e => {
                    const payload = JSON.parse(e.data);
                    for (let i = 0; i < _jobs_all.length; i++) {
                        if (_jobs_all[i].id === payload.id) {
                            _jobs_all[i].preview_path = payload.preview_path;
                            break;
                        }
                    }
                    _renderJobsPage();
                });

                _es.addEventListener('status_update', e => {
                    const s = JSON.parse(e.data);
                    if (s.uptime !== undefined) document.getElementById('uptime').textContent = formatUptime(s.uptime);
                    if (s.printer_info !== undefined) document.getElementById('printer-info').textContent = s.printer_info;
                    if (s.jobs_processed !== undefined) document.getElementById('jobs-count').textContent = s.jobs_processed;
                });

                _es.onopen = () => {
                    _sse_backoff = 1000;
                };

                _es.onerror = (err) => {
                    console.warn('SSE error', err);
                    try { if (_es) _es.close(); } catch (e) {}
                    _es = null;
                    // One-off fetch to resync then reconnect with backoff
                    refreshStatus();
                    const delay = _sse_backoff;
                    _sse_backoff = Math.min(_sse_backoff * 2, _sse_backoff_max);
                    setTimeout(setupSSE, delay);
                };
            } catch (e) {
                console.warn('SSE setup failed; doing one-time status fetch', e);
                refreshStatus();
            }
        }

        if (_sse_supported) {
            setupSSE();
        } else {
            // No SSE: one-time snapshot fetch
            refreshStatus();
        }
    </script>
</body>
</html>
        """
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_status(self):
        """Serve status information as JSON"""
        try:
            logging.debug(f"[WebServer] /api/status requested from {self.client_address[0]}")
            # include recent jobs from history (most recent first)
            with self.bridge.history_lock:
                recent = list(self.bridge.job_history)[-100:]
            jobs = [{
                'id': r['id'],
                'timestamp': r['timestamp'],
                'size': r['size'],
                'preview_path': r['preview_path']
            } for r in reversed(recent)]

            status = {
                'boot_time': self.bridge.boot_time,
                'uptime': time.time() - self.bridge.boot_time,
                'emulator_ip': self.bridge.get_emulator_ip(),
                'printer_info': self.bridge.get_printer_info(),
                'jobs_processed': self.bridge.jobs_processed,
                'last_job': self.bridge.get_last_job_info(),
                'recent_jobs': jobs
            }
            
            response_data = json.dumps(status).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', len(response_data))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response_data)
            
        except Exception as e:
            logging.error(f"[WebServer] Error serving status: {e}")
            self.send_error(500)

    def serve_static_file(self):
        """Serve static files from static directory"""
        filename = os.path.basename(self.path)
        filepath = os.path.join('static', filename)
        
        if os.path.exists(filepath):
            try:
                with open(filepath, 'rb') as f:
                    self.send_response(200)
                    if filename.endswith('.ico'):
                        self.send_header('Content-type', 'image/x-icon')
                    self.send_header('Cache-Control', 'public, max-age=31536000')
                    self.end_headers()
                    self.wfile.write(f.read())
            except Exception as e:
                logging.error(f"[WebServer] Error serving static file: {e}")
                self.send_error(500)
        else:
            self.send_error(404)

    def serve_image(self):
        """Serve image files from print_jobs directory"""
        filename = os.path.basename(self.path)
        filepath = os.path.join('print_jobs', filename)
        
        if os.path.exists(filepath) and filename.endswith('.png'):
            try:
                with open(filepath, 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                    self.send_header('Pragma', 'no-cache')
                    self.send_header('Expires', '0')
                    self.end_headers()
                    self.wfile.write(f.read())
            except Exception as e:
                self.send_error(500)
            else:
                self.send_error(404)

    def serve_sse(self):
        """Serve Server-Sent Events for push updates"""
        logging.info(f"[WebServer] SSE connection from {self.client_address[0]}")
        # Prepare a per-client queue to hold outgoing messages
        q = queue.Queue(maxsize=100)

        # Register client
        try:
            clients = getattr(self.bridge, '_sse_clients', None)
            if clients is None:
                # initialize on bridge
                self.bridge._sse_clients = set()
                clients = self.bridge._sse_clients
            clients.add(q)
        except Exception as e:
            logging.debug(f"[WebServer] Could not register SSE client: {e}")

        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            # Send an initial snapshot event so client can populate UI
            try:
                with self.bridge.history_lock:
                    recent = list(self.bridge.job_history)[-100:]
                jobs = [{ 'id': r['id'], 'timestamp': r['timestamp'], 'size': r['size'], 'preview_path': r['preview_path'] } for r in reversed(recent)]
                snapshot = {
                    'boot_time': self.bridge.boot_time,
                    'uptime': time.time() - self.bridge.boot_time,
                    'emulator_ip': self.bridge.get_emulator_ip(),
                    'printer_info': self.bridge.get_printer_info(),
                    'jobs_processed': self.bridge.jobs_processed,
                    'recent_jobs': jobs
                }
                init_msg = f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
                self.wfile.write(init_msg.encode('utf-8'))
                self.wfile.flush()
            except Exception:
                pass

            # Writer loop: stream queued messages until client disconnects
            while True:
                try:
                    msg = q.get(timeout=30)
                except queue.Empty:
                    # heartbeat to keep connection alive
                    try:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        continue
                    except Exception:
                        break
                try:
                    self.wfile.write(msg)
                    self.wfile.flush()
                except Exception:
                    break

        finally:
            # cleanup
            try:
                clients.remove(q)
            except Exception:
                pass

    def handle_reprint(self):
        """Handle reprint request"""
        try:
            logging.info("[WebServer] Received reprint request")
            
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            job_id = None
            if content_length > 0:
                body = self.rfile.read(content_length)
                logging.debug(f"[WebServer] Request body: {body}")
                try:
                    parsed = json.loads(body.decode('utf-8'))
                    job_id = parsed.get('job_id')
                except Exception:
                    pass

            if job_id:
                success = self.bridge.reprint_job(job_id)
                msg = 'Job queued for printing' if success else 'Job not found'
            else:
                success = self.bridge.reprint_last_job()
                msg = 'Last job queued for printing' if success else 'No job available to reprint'

            response = {'success': success, 'message': msg}
            
            logging.info(f"[WebServer] Reprint response: {response}")
            
        except Exception as e:
            logging.error(f"[WebServer] Reprint error: {e}")
            response = {
                'success': False,
                'message': f'Error: {str(e)}'
            }
        
        # Send response
        try:
            response_data = json.dumps(response).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', len(response_data))
            self.send_header('Access-Control-Allow-Origin', '*')  # Add CORS header
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            self.wfile.write(response_data)
        except Exception as e:
            logging.error(f"[WebServer] Error sending response: {e}")

    def handle_restart_printer(self):
        """Handle restart printer request"""
        try:
            logging.info("[WebServer] Received restart printer request")
            success = False
            msg = 'Restart initiated'
            try:
                success = self.bridge.restart_printer_connection()
                if not success:
                    msg = 'Printer restart not available'
            except Exception as e:
                logging.error(f"[WebServer] Error restarting printer: {e}")
                success = False
                msg = f'Error: {e}'

            response = {'success': success, 'message': msg}
        except Exception as e:
            logging.error(f"[WebServer] Restart error: {e}")
            response = {'success': False, 'message': str(e)}

        try:
            response_data = json.dumps(response).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', len(response_data))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            self.wfile.write(response_data)
        except Exception as e:
            logging.error(f"[WebServer] Error sending restart response: {e}")

class BridgeEpsonToESCPOS:
    def __init__(self, format="image", printer_ip=None, printer_port=9100, vendor_id=None, product_id=None, web_port=8080):
        self.print_queue = queue.Queue()
        # threadpool for CPU / IO-bound background tasks (image generation, etc.)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        # full path to last generated preview file on disk (for targeted cleanup)
        self._last_preview_filename = None
        # History of recent jobs (keeps records for last 12h)
        self.history_lock = threading.Lock()
        self.history_retention_seconds = 12 * 3600
        self.job_history = deque()  # each item: dict {id, timestamp, size, data_path, preview_path}
        self.printer_ip = printer_ip
        self.printer_port = printer_port
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.emulator = None
        # Lock protecting access to active printer object and restart signalling
        self._printer_lock = threading.Lock()
        # Event set when a restart is requested; worker checks this and reconnects
        self._printer_restart_event = threading.Event()
        # Keep a weak reference to the active printer object if available
        self._active_printer = None
        self.running = True
        self.format = format
        self.connection_type = "network" if printer_ip else "usb"
        self.web_port = web_port
        self.jobs_processed = 0
        self.last_job_data = None
        self.last_job_timestamp = None
        self.last_job_preview = None
        self.boot_time = time.time()  # Record boot time
        self.emulator_ip = None  # Initialize emulator_ip

    def get_local_ip(self):
        """Get the local IP address of this machine"""
        try:
            # Connect to a dummy address to determine local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def get_emulator_ip(self):
        """Get the emulator IP address safely"""
        if self.emulator_ip:
            return self.emulator_ip
        elif self.emulator and hasattr(self.emulator, 'ip') and self.emulator.ip:
            return self.emulator.ip
        else:
            return self.get_local_ip()

    def on_print_job_complete(self, escpos_data):
        """Callback from Epson emulator when a print job is received"""
        logging.info("[Bridge] Received print job, queueing...")
        self.last_job_data = escpos_data
        now = time.time()
        self.last_job_timestamp = now
        self.jobs_processed += 1
        # Persist raw job to disk asynchronously and add to history immediately
        job_id = uuid.uuid4().hex
        data_filename = os.path.join('print_jobs', f"job_{job_id}.bin")
        size = len(escpos_data) if isinstance(escpos_data, (bytes, bytearray, str)) else None

        def _write_data():
            try:
                # Best-effort: prefer to write the original raw bytes.
                if isinstance(escpos_data, (bytes, bytearray)):
                    b = bytes(escpos_data)
                elif isinstance(escpos_data, str):
                    # If it's a hex string, convert to bytes; otherwise encode utf-8
                    s = escpos_data.strip()
                    s_clean = s.replace(" ", "")
                    try:
                        if all(c in "0123456789abcdefABCDEF" for c in s_clean) and len(s_clean) % 2 == 0:
                            b = bytes.fromhex(s_clean)
                        else:
                            raise ValueError("not-hex")
                    except Exception:
                        b = s.encode('utf-8')
                else:
                    # Try to build bytes from iterable (e.g. list of ints). Fallback to utf-8 of str()
                    try:
                        b = bytes(escpos_data)
                    except Exception:
                        b = str(escpos_data).encode('utf-8')

                with open(data_filename, 'wb') as f:
                    f.write(b)
                return True
            except Exception as e:
                logging.debug(f"[Bridge] Could not write job data to disk: {e}")
                return False

        # Add lightweight history entry now; preview_path updated later by callback
        job_record = {
            'id': job_id,
            'timestamp': now,
            'size': size or 0,
            'data_path': data_filename,
            'preview_path': None
        }
        with self.history_lock:
            self.job_history.append(job_record)
            # prune old entries synchronously to avoid unbounded growth
            self._prune_history_locked()

        # Notify SSE clients about new job (non-blocking)
        try:
            payload = {
                'id': job_id,
                'timestamp': now,
                'size': size or 0,
                'preview_path': None
            }
            try:
                clients = getattr(self, '_sse_clients', set())
                msg = f"event: new_job\ndata: {json.dumps(payload)}\n\n".encode('utf-8')
                for q in list(clients):
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        # drop slow client
                        pass
            except Exception:
                pass
        except Exception:
            pass

        # async write and preview generation (preview callback updates job record)
        try:
            self.executor.submit(_write_data)
        except Exception as e:
            logging.debug(f"[Bridge] Could not submit job data write task: {e}")

        # Generate preview PNG asynchronously so this callback returns quickly
        def _preview_done(fut):
            try:
                preview_path = fut.result()
                if preview_path:
                    # name preview file with job id so it's tied to the job record
                    new_filename = f"preview_{job_id}.png"
                    new_path = os.path.join('print_jobs', new_filename)
                    try:
                        os.replace(preview_path, new_path)
                    except Exception:
                        # fallback to rename
                        os.rename(preview_path, new_path)

                    # update corresponding job record
                    try:
                        with self.history_lock:
                            for rec in reversed(self.job_history):
                                if rec['id'] == job_id:
                                    # remove prior preview file if present and different
                                    old = rec.get('preview_path')
                                    if old and old != f"/print_jobs/{new_filename}":
                                        try:
                                            old_fp = old.replace('/print_jobs/', 'print_jobs/')
                                            if os.path.exists(old_fp):
                                                os.remove(old_fp)
                                        except Exception:
                                            pass
                                    rec['preview_path'] = f"/print_jobs/{new_filename}"
                                    # update in-memory last_job_preview if this is the most recent
                                    if self.last_job_timestamp == now:
                                        self.last_job_preview = rec['preview_path']
                                    break
                    except Exception as e:
                        logging.debug(f"[Bridge] Could not update job record with preview: {e}")

                    logging.info(f"[Bridge] Preview generated: {new_path}")
                    # notify SSE clients that preview is available for this job
                    try:
                        payload = { 'id': job_id, 'preview_path': f"/print_jobs/{new_filename}" }
                        clients = getattr(self, '_sse_clients', set())
                        msg = f"event: job_updated\ndata: {json.dumps(payload)}\n\n".encode('utf-8')
                        for q in list(clients):
                            try:
                                q.put_nowait(msg)
                            except queue.Full:
                                pass
                    except Exception:
                        pass
            except Exception as e:
                logging.debug(f"[Bridge] Preview generation failed: {e}")

        try:
            fut = self.executor.submit(
                escpos_graphics_converter.generate_merged_bitmap_png,
                escpos_data,
                output_dir="print_jobs",
                debug=False
            )
            fut.add_done_callback(_preview_done)
        except Exception as e:
            logging.debug(f"[Bridge] Could not submit preview task: {e}")
        
        self.print_queue.put(escpos_data)
        # Ensure history pruning also runs in background (best-effort)
        try:
            self.executor.submit(self._prune_history)
        except Exception:
            pass

    def get_printer_info(self):
        """Get printer connection information"""
        if self.connection_type == "network":
            return f"Network ({self.printer_ip}:{self.printer_port})"
        else:
            return f"USB (VID: {self.vendor_id:04x}, PID: {self.product_id:04x})"

    def get_last_job_info(self):
        """Get information about the last print job"""
        with self.history_lock:
            if not self.job_history:
                return None
            rec = self.job_history[-1]
            return {
                'id': rec['id'],
                'timestamp': rec['timestamp'],
                'size': rec['size'],
                'preview_path': rec['preview_path']
            }

    def reprint_job(self, job_id):
        """Queue a saved job by id for reprinting"""
        with self.history_lock:
            for rec in reversed(self.job_history):
                if rec['id'] == job_id:
                    data_path = rec.get('data_path')
                    preview_path = rec.get('preview_path')
                    break
            else:
                return False

        try:
            with open(data_path, 'rb') as f:
                data = f.read()
            # If we're in image mode and preview exists, ensure worker uses that preview
            if self.format == "image" and preview_path:
                try:
                    # preview_path is stored as "/print_jobs/preview_<id>.png"
                    self.last_job_preview = preview_path
                except Exception:
                    pass

            self.print_queue.put(data)
            logging.info(f"[Bridge] Requeued job {job_id} for printing")
            return True
        except Exception as e:
            logging.debug(f"[Bridge] Could not read saved job data for reprint: {e}")
            return False

    def _prune_history_locked(self):
        """Assumes history_lock is held. Remove entries older than retention and delete files."""
        cutoff = time.time() - self.history_retention_seconds
        while self.job_history and self.job_history[0]['timestamp'] < cutoff:
            old = self.job_history.popleft()
            # try to delete files
            try:
                if old.get('data_path') and os.path.exists(old['data_path']):
                    os.remove(old['data_path'])
            except Exception:
                pass
            try:
                if old.get('preview_path'):
                    fp = old['preview_path'].replace('/print_jobs/', 'print_jobs/')
                    if os.path.exists(fp):
                        os.remove(fp)
            except Exception:
                pass

    def _prune_history(self):
        """Thread-safe prune wrapper"""
        try:
            with self.history_lock:
                self._prune_history_locked()
        except Exception:
            pass

    def reprint_last_job(self):
        """Queue the last job for reprinting"""
        if not self.last_job_data:
            return False
        
        logging.info("[Bridge] Reprinting last job...")
        self.print_queue.put(self.last_job_data)
        return True

    def restart_printer_connection(self):
        """Signal the printer worker to close and reopen the printer connection.

        Returns True if the signal was sent; False if no worker appears to be running.
        """
        # Set the restart event so worker will drop connection and recreate it.
        try:
            logging.info("[Bridge] Restarting printer connection requested")
            # Set the event and also attempt to close the current active printer
            self._printer_restart_event.set()
            with self._printer_lock:
                if self._active_printer is not None:
                    try:
                        close_fn = getattr(self._active_printer, 'close', None)
                        if callable(close_fn):
                            close_fn()
                    except Exception as e:
                        logging.debug(f"[Bridge] Error closing active printer during restart: {e}")
            # Clear event after a small delay to allow worker to notice it
            # Worker will clear the event after recreating connection
            return True
        except Exception as e:
            logging.error(f"[Bridge] restart_printer_connection failed: {e}")
            return False

    def start_web_server(self):
        """Start the web server"""
        def create_handler(*args, **kwargs):
            return WebInterface(self, *args, **kwargs)

        def run_server():
            try:
                server = ThreadingHTTPServer(('0.0.0.0', self.web_port), create_handler)
                web_ip = self.get_emulator_ip()
                logging.info(f"[Bridge] Web interface available at http://{web_ip}:{self.web_port} (ThreadingHTTPServer)")
                server.serve_forever()
            except Exception as e:
                logging.error(f"[Bridge] Web server error: {e}")

        self.web_thread = threading.Thread(target=run_server, daemon=True)
        self.web_thread.start()

    def stop(self):
        """Gracefully stop the bridge"""
        logging.info("[Bridge] Stopping...")
        self.running = False
        try:
            # best-effort shutdown of executor
            self.executor.shutdown(wait=False)
        except Exception:
            pass

    def start_emulator(self, ip=None, mac=None, netmask=None, gateway=None):
        """Start the Epson emulator in a thread, with print job callback"""
        def run_emulator():
            # Patch the emulator to use our callback
            class PatchedEpsonTM30Emulator(epson_emulator.EpsonTM30Emulator):
                def on_print_job_complete(self_inner, escpos_data):
                    self.on_print_job_complete(escpos_data)
            
            # Use provided IP or get local IP
            if ip:
                self.emulator_ip = ip
            else:
                self.emulator_ip = self.get_local_ip()
            
            # Create and start emulator
            emulator = PatchedEpsonTM30Emulator(self.emulator_ip, mac, netmask, gateway)
            self.emulator = emulator
            emulator.start()

        t = threading.Thread(target=run_emulator, daemon=True)
        t.start()

    def start_printer_worker(self):
        """Start a thread that keeps the ESC/POS printer connected and prints from the queue"""
        def run_worker():
            while self.running:
                printer = None
                try:
                    # Create printer connection based on connection type
                    if self.connection_type == "network":
                        printer = Network(
                            host=self.printer_ip,
                            port=self.printer_port,
                            timeout=5
                        )
                        logging.info(f"[Bridge] Connected to ESC/POS printer at {self.printer_ip}")
                    else:  # USB connection
                        printer = Usb(
                            self.vendor_id,
                            self.product_id
                        )
                        logging.info(f"[Bridge] Connected to USB ESC/POS printer (VID: {self.vendor_id:04x}, PID: {self.product_id:04x})")

                    # Defensive: ensure profile exists before setting
                    try:
                        if hasattr(printer, "profile") and printer.profile is not None:
                            printer.profile.media_width_pixel = 576  # Standard width for 80mm thermal printers
                    except Exception as e:
                        logging.debug(f"[Bridge] Could not set media width on printer profile: {e}")

                    # store active printer reference so external callers can close it
                    with self._printer_lock:
                        self._active_printer = printer

                    # Print ready message with boot time
                    connection_method = f"Network ({self.printer_ip})" if self.connection_type == "network" else f"USB (VID: {self.vendor_id:04x}, PID: {self.product_id:04x})"
                    boot_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.boot_time))
                    ready_message = f"Connected to printer via {connection_method}.\n"
                    ready_message += f"Boot time: {boot_time_str}\n"
                    emulator_ip = self.get_emulator_ip()
                    ready_message += f"Emulator running at {emulator_ip}\n"
                    ready_message += f"Web interface: http://{emulator_ip}:{self.web_port}\n"
                    try:
                        printer.text(ready_message)
                        printer.cut()
                        logging.info("[Bridge] Printed ready message")
                    except Exception as e:
                        logging.warning(f"[Bridge] Unable to print ready message: {e}")

                    # Main printing loop for this connection
                    while self.running:
                        # If a restart has been requested, break to outer loop to recreate connection
                        if self._printer_restart_event.is_set():
                            logging.info("[Bridge] Printer restart event detected, reconnecting printer connection")
                            # clear the event here and break to recreate connection
                            try:
                                self._printer_restart_event.clear()
                            except Exception:
                                pass
                            break
                        try:
                            escpos_data = self.print_queue.get(timeout=1)
                        except queue.Empty:
                            continue

                        logging.info(f"[Bridge] Printing received data using {self.format} format...")
                        try:
                            if self.format == "image":
                                # Use the same preview image for printing
                                png_path = None
                                if self.last_job_preview:
                                    png_path = self.last_job_preview.replace('/print_jobs/', 'print_jobs/')
                                if png_path and os.path.exists(png_path):
                                    logging.info(f"[Bridge] Using preview PNG for printing: {png_path}")
                                    # Print the image from the PNG file
                                    try:
                                        printer.text("\n")
                                    except Exception:
                                        # some drivers don't like text before image; ignore
                                        pass
                                    try:
                                        printer.image(png_path)
                                        printer.cut()
                                    except Exception as img_err:
                                        # Detect USB device disconnects and escalate so outer handler reconnects
                                        try:
                                            import usb
                                            if isinstance(img_err, usb.core.USBError):
                                                logging.exception("[Bridge] USB device error during image print - treating as disconnect")
                                                raise
                                        except Exception:
                                            # Not a usb.core.USBError or usb not available — re-raise original
                                            raise
                                else:
                                    logging.warning("[Bridge] No preview PNG available for image print")
                            elif self.format == "raw":
                                # Accept bytes, hex string, or plain string
                                raw_data = escpos_data
                                if isinstance(raw_data, (bytes, bytearray)):
                                    raw_bytes = bytes(raw_data)
                                elif isinstance(raw_data, str):
                                    s = raw_data.strip()
                                    try:
                                        # allow space separated hex
                                        s_clean = s.replace(" ", "")
                                        raw_bytes = bytes.fromhex(s_clean)
                                    except ValueError:
                                        raw_bytes = s.encode('utf-8')
                                else:
                                    try:
                                        raw_bytes = bytes(raw_data)
                                    except Exception:
                                        raw_bytes = bytes(str(raw_data), 'utf-8')

                                # Send raw ESC/POS commands directly
                                try:
                                    # prefer official _raw if available
                                    if hasattr(printer, "_raw"):
                                        try:
                                            printer._raw(raw_bytes)
                                        except Exception as write_err:
                                            # If this is a USB disconnect, escalate so outer handler reconnects
                                            try:
                                                import usb
                                                if isinstance(write_err, usb.core.USBError):
                                                    logging.exception("[Bridge] USB device error during raw write - treating as disconnect")
                                                    raise
                                            except Exception:
                                                # Not a usb.core.USBError or usb not available — re-raise original
                                                raise
                                            # If not USBError, re-raise to be handled below
                                            raise
                                    else:
                                        # fallback to write()/text if necessary
                                        try:
                                            printer.device.write(raw_bytes)
                                        except Exception as dev_write_err:
                                            try:
                                                import usb
                                                if isinstance(dev_write_err, usb.core.USBError):
                                                    logging.exception("[Bridge] USB device error during device.write - treating as disconnect")
                                                    raise
                                            except Exception:
                                                # Not a usb.core.USBError or usb not available — try text fallback
                                                pass
                                            # last resort: send as text
                                            try:
                                                printer.text(raw_bytes.decode('latin-1', errors='ignore'))
                                            except Exception:
                                                raise
                                    logging.info("[Bridge] Raw ESC/POS data sent")
                                except Exception:
                                    # escalate to outer handler which will requeue and reconnect
                                    raise

                            logging.info("[Bridge] Print job completed")
                        except Exception as e:
                            # Log full exception with traceback to help diagnose printing issues
                            logging.exception("[Bridge] Error printing")
                            # Requeue this job for retry (fast, thread-safe)
                            try:
                                self.print_queue.put(escpos_data)
                            except Exception as q_e:
                                logging.debug(f"[Bridge] Could not requeue failed job quickly: {q_e}")

                            # Close/cleanup this printer connection and break to recreate it
                            if printer is not None:
                                # Try to call the printer's close method if available
                                try:
                                    close_fn = getattr(printer, "close", None)
                                    if callable(close_fn):
                                        close_fn()
                                except Exception as close_err:
                                    logging.debug(f"[Bridge] Error calling printer.close(): {close_err}")
                                    # Best-effort: try to reset USB device handles if we have vendor/product ids
                                    try:
                                        import usb
                                        try:
                                            devs = usb.core.find(find_all=True, idVendor=self.vendor_id, idProduct=self.product_id)
                                            for d in devs:
                                                try:
                                                    d.reset()
                                                except Exception as e:
                                                    logging.debug(f"[Bridge] Error resetting USB device: {e}")
                                        except Exception as e:
                                            logging.debug(f"[Bridge] Error finding/resetting USB devices: {e}")
                                    except Exception as e:
                                        logging.debug(f"[Bridge] USB module not available or other error: {e}")
                            # break inner loop to recreate connection
                            break

                except Exception as e:
                    logging.exception("[Bridge] Printer connection error")
                finally:
                    # Ensure printer is closed before retrying
                    try:
                        if printer is not None:
                            # clear active printer reference while closing
                            with self._printer_lock:
                                try:
                                    close_fn = getattr(printer, "close", None)
                                    if callable(close_fn):
                                        close_fn()
                                except Exception as e:
                                    logging.debug(f"[Bridge] Error closing printer in finally: {e}")
                                finally:
                                    # ensure _active_printer unset
                                    try:
                                        if self._active_printer is printer:
                                            self._active_printer = None
                                    except Exception:
                                        pass
                    except Exception as e:
                        logging.debug(f"[Bridge] Error closing printer in finally: {e}")

                # Wait briefly before attempting to reconnect
                if self.running:
                    logging.info("[Bridge] Waiting 2s before retrying printer connection...")
                    time.sleep(2)

        t = threading.Thread(target=run_worker, daemon=True)
        t.start()

    def run(self, ip=None, mac=None, netmask=None, gateway=None):
        # Ensure print_jobs directory exists
        os.makedirs("print_jobs", exist_ok=True)
        # Load persisted jobs from disk into memory so UI shows history after restart
        try:
            with self.history_lock:
                # find job files and associated previews
                files = os.listdir('print_jobs')
                jobs = []
                for fn in files:
                    if fn.startswith('job_') and fn.endswith('.bin'):
                        job_id = fn[len('job_'):-len('.bin')]
                        data_path = os.path.join('print_jobs', fn)
                        # try to stat file for timestamp/size
                        try:
                            st = os.stat(data_path)
                            timestamp = st.st_mtime
                            size = st.st_size
                        except Exception:
                            timestamp = time.time()
                            try:
                                size = os.path.getsize(data_path)
                            except Exception:
                                size = 0

                        preview_name = f"preview_{job_id}.png"
                        preview_path = None
                        if preview_name in files:
                            preview_path = f"/print_jobs/{preview_name}"

                        jobs.append({
                            'id': job_id,
                            'timestamp': timestamp,
                            'size': size,
                            'data_path': data_path,
                            'preview_path': preview_path
                        })

                # sort by timestamp ascending (oldest first), then append while pruning
                jobs.sort(key=lambda x: x['timestamp'])
                now = time.time()
                cutoff = now - self.history_retention_seconds
                for rec in jobs:
                    if rec['timestamp'] < cutoff:
                        # skip old entries beyond retention
                        continue
                    self.job_history.append(rec)
        except Exception as e:
            logging.debug(f"[Bridge] Could not load persisted jobs: {e}")
        
        boot_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.boot_time))
        logging.info(f"[Bridge] Starting bridge at {boot_time_str}")
        
        # Start emulator first to set the IP
        self.start_emulator(ip, mac, netmask, gateway)
        
        # Give emulator a moment to initialize
        time.sleep(1)
        
        # Start web server and printer worker
        self.start_web_server()
        self.start_printer_worker()
        
        logging.info("[Bridge] Epson emulator, printer worker, and web server started.")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("[Bridge] Shutting down...")
            self.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Epson TM-m30 to ESC/POS Bridge with Web Interface")
    # Printer connection options
    connection_group = parser.add_mutually_exclusive_group(required=True)
    connection_group.add_argument("--printer-ip", help="IP address of the target ESC/POS printer")
    connection_group.add_argument("--usb", action="store_true", help="Use USB printer connection")
    
    parser.add_argument("--printer-port", type=int, default=9100, help="Port of the target ESC/POS printer (network only)")
    parser.add_argument("--vendor-id", type=lambda x: int(x, 16), help="USB Vendor ID in hex (required for USB)")
    parser.add_argument("--product-id", type=lambda x: int(x, 16), help="USB Product ID in hex (required for USB)")
    parser.add_argument("--format", choices=["image", "raw"], default="image", help="Print format: 'image' (convert to image) or 'raw' (direct ESC/POS)")
    parser.add_argument("--web-port", type=int, default=8080, help="Web interface port (default: 8080)")
    parser.add_argument("--ip", default=None, help="Epson emulator IP address")
    parser.add_argument("--mac", default=None, help="Epson emulator MAC address")
    parser.add_argument("--netmask", default=None, help="Epson emulator netmask")
    parser.add_argument("--gateway", default=None, help="Epson emulator gateway")
    args = parser.parse_args()

    # Validate USB arguments if USB mode is selected
    if args.usb:
        if args.vendor_id is None or args.product_id is None:
            parser.error("--vendor-id and --product-id are required when using USB connection")
    
    bridge = BridgeEpsonToESCPOS(
        format=args.format,
        printer_ip=args.printer_ip,
        printer_port=args.printer_port,
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        web_port=args.web_port
    )
    bridge.run(ip=args.ip, mac=args.mac, netmask=args.netmask, gateway=args.gateway)
