#!/usr/bin/env python3
"""
Epson TM-m30 to ESC/POS Bridge

- Emulates an Epson TM-m30 printer (receives ESC/POS data)
- Converts ESC/POS data to PNG
- Forwards the data to a real ESC/POS printer
- Runs both the emulator and printer connection continuously

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
from escpos.printer import Network

# Import Epson emulator and graphics converter logic
from mix import epson_emulator
from mix import escpos_graphics_converter

class BridgeEpsonToESCPOS:
    def __init__(self, printer_ip, printer_port=9100, format="image"):
        self.print_queue = queue.Queue()
        self.printer_ip = printer_ip
        self.printer_port = printer_port
        self.emulator = None
        self.running = True
        self.format = format

    def on_print_job_complete(self, escpos_data):
        """Callback from Epson emulator when a print job is received"""
        print(f"[Bridge] Received print job, queueing...")
        self.print_queue.put(escpos_data)

    def start_emulator(self, ip=None, mac=None, netmask=None, gateway=None):
        """Start the Epson emulator in a thread, with print job callback"""
        def run_emulator():
            # Patch the emulator to use our callback
            class PatchedEpsonTM30Emulator(epson_emulator.EpsonTM30Emulator):
                def on_print_job_complete(self_inner, escpos_data):
                    self.on_print_job_complete(escpos_data)
            # Use provided or default network info
            emulator = PatchedEpsonTM30Emulator(ip, mac, netmask, gateway)
            self.emulator = emulator
            emulator.start()
        t = threading.Thread(target=run_emulator, daemon=True)
        t.start()

    def start_printer_worker(self):
        """Start a thread that keeps the ESC/POS printer connected and prints from the queue"""
        def run_worker():
            while self.running:
                try:
                    # Create printer connection
                    printer = Network(
                        host=self.printer_ip,
                        port=self.printer_port,
                        timeout=5
                    )
                    # Set printer capabilities
                    printer.profile.media_width_pixel = 576  # Standard width for 80mm thermal printers
                    print(f"[Bridge] Connected to ESC/POS printer at {self.printer_ip}")

                    while self.running:
                        try:
                            escpos_data = self.print_queue.get(timeout=1)
                        except queue.Empty:
                            time.sleep(0.1)
                            continue

                        print(f"[Bridge] Printing received data using {self.format} format...")
                        try:
                            if self.format == "image":
                                # Generate PNG for debug purposes
                                png_path = escpos_graphics_converter.generate_merged_bitmap_png(
                                    escpos_data, output_dir="print_jobs", debug=False
                                )
                                if png_path:
                                    print(f"[Bridge] Debug PNG generated: {png_path}")
                                    # Initialize the printer
                                    printer.text("\n")  # Start with a clean line
                                    # Print the image from the PNG file
                                    printer.image(png_path)
                                    # Cut the paper
                                    printer.cut()
                            elif self.format == "raw":
                                # Convert hex string to bytes
                                raw_data = bytes.fromhex(escpos_data)
                                # Send raw ESC/POS commands directly
                                printer._raw(raw_data)
                                print("[Bridge] Raw ESC/POS data sent")
                            
                            print("[Bridge] Print job completed")

                        except Exception as e:
                            print(f"[Bridge] Error printing: {e}")

                except Exception as e:
                    print(f"[Bridge] Printer connection error: {e}")
                    time.sleep(10)  # Wait before retrying connection

        t = threading.Thread(target=run_worker, daemon=True)
        t.start()

    def run(self, ip=None, mac=None, netmask=None, gateway=None):
        self.start_emulator(ip, mac, netmask, gateway)
        self.start_printer_worker()
        print("[Bridge] Epson emulator and printer worker started. Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[Bridge] Shutting down...")
            self.running = False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Epson TM-m30 to ESC/POS Bridge")
    parser.add_argument("--printer-ip", required=True, help="IP address of the target ESC/POS printer")
    parser.add_argument("--printer-port", type=int, default=9100, help="Port of the target ESC/POS printer")
    parser.add_argument("--format", choices=["image", "raw"], default="image", help="Print format: 'image' (convert to image) or 'raw' (direct ESC/POS)")
    parser.add_argument("--ip", default=None, help="Epson emulator IP address")
    parser.add_argument("--mac", default=None, help="Epson emulator MAC address")
    parser.add_argument("--netmask", default=None, help="Epson emulator netmask")
    parser.add_argument("--gateway", default=None, help="Epson emulator gateway")
    args = parser.parse_args()

    bridge = BridgeEpsonToESCPOS(
        printer_ip=args.printer_ip,
        printer_port=args.printer_port,
        format=args.format
    )
    bridge.run(ip=args.ip, mac=args.mac, netmask=args.netmask, gateway=args.gateway)
