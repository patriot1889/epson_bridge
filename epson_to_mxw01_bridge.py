#!/usr/bin/env python3
"""
Epson TM-m30 to MXW01 Bridge

- Emulates an Epson TM-m30 printer (receives ESC/POS data)
- Converts ESC/POS data to PNG
- Sends PNG to MXW01 printer for printing
- Runs both the emulator and MXW01 connection continuously

Requirements:
- bleak
- Pillow

Usage:
    python epson_to_mxw01_bridge.py --mxw01-device MXW01
"""
import os
import threading
import time
import queue
import argparse

# Import Epson emulator and graphics converter logic
from mix import epson_emulator
from mix import escpos_graphics_converter
from mix import mxw01_printer

class BridgeEpsonToMXW01:
    def __init__(self, mxw01_device="MXW01", intensity=0x5D, print_mode="monochrome",
                 eject_before=None, eject_after=None, retract_before=None, retract_after=None, rotate_180=False):
        self.print_queue = queue.Queue()
        self.mxw01_device = mxw01_device
        self.intensity = intensity
        self.print_mode = print_mode
        self.eject_before = eject_before
        self.eject_after = eject_after
        self.retract_before = retract_before
        self.retract_after = retract_after
        self.rotate_180 = rotate_180
        self.mxw01 = None
        self.emulator = None
        self.running = True

    def on_print_job_complete(self, escpos_data):
        """Callback from Epson emulator when a print job is received"""
        timestamp = int(time.time())
        # Convert ESC/POS data to PNG
        png_path = escpos_graphics_converter.generate_merged_bitmap_png(
            escpos_data, output_dir="print_jobs", debug=False
        )
        if png_path:
            print(f"[Bridge] PNG generated: {png_path}")
            self.print_queue.put(png_path)
        else:
            print("[Bridge] No PNG generated from print job")

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

    def start_mxw01_worker(self):
        """Start a thread that keeps the MXW01 printer connected and prints PNGs from the queue"""
        import asyncio
        def run_worker():
            asyncio.run(self.mxw01_worker())
        t = threading.Thread(target=run_worker, daemon=True)
        t.start()

    async def mxw01_worker(self):
        import sys
        import asyncio
        from PIL import Image
        # Map print_mode string to enum
        mode_map = {
            'monochrome': mxw01_printer.PrintMode.MONOCHROME,
            'monochrome_2': mxw01_printer.PrintMode.MONOCHROME_2,
            'grayscale': mxw01_printer.PrintMode.GRAYSCALE
        }
        print_mode = mode_map.get(self.print_mode, mxw01_printer.PrintMode.MONOCHROME)
        printer = mxw01_printer.MXW01Printer(self.mxw01_device)
        print(f"[Bridge] Connecting to MXW01 printer: {self.mxw01_device}")
        while self.running:
            try:
                connected = await printer.scan_and_connect()
                if not connected:
                    print("[Bridge] Could not connect to MXW01 printer, retrying in 10s...")
                    await asyncio.sleep(10)
                    continue
                print("[Bridge] Connected to MXW01 printer")
                self.mxw01 = printer
                while self.running:
                    try:
                        png_path = self.print_queue.get(timeout=1)
                    except queue.Empty:
                        await asyncio.sleep(0.1)
                        continue
                    print(f"[Bridge] Printing PNG: {png_path}")
                    try:
                        # Eject/retract before
                        if self.eject_before:
                            print(f"[Bridge] Ejecting {self.eject_before} lines before printing...")
                            await printer.eject_paper(self.eject_before)
                        if self.retract_before:
                            print(f"[Bridge] Retracting {self.retract_before} lines before printing...")
                            await printer.retract_paper(self.retract_before)

                        # Rotate image if requested
                        img_path_to_print = png_path
                        if self.rotate_180:
                            try:
                                img = Image.open(png_path)
                                img = img.rotate(180)
                                rotated_path = png_path.replace('.png', '_rotated.png')
                                img.save(rotated_path)
                                img_path_to_print = rotated_path
                                print(f"[Bridge] Rotated image saved: {rotated_path}")
                            except Exception as e:
                                print(f"[Bridge] Error rotating image: {e}")

                        await printer.print_image(img_path_to_print, intensity=self.intensity, mode=print_mode)
                        print(f"[Bridge] Print sent to MXW01: {img_path_to_print}")

                        # Eject/retract after
                        if self.eject_after:
                            print(f"[Bridge] Ejecting {self.eject_after} lines after printing...")
                            await printer.eject_paper(self.eject_after)
                        if self.retract_after:
                            print(f"[Bridge] Retracting {self.retract_after} lines after printing...")
                            await printer.retract_paper(self.retract_after)
                    except Exception as e:
                        print(f"[Bridge] Error printing on MXW01: {e}")
            except Exception as e:
                print(f"[Bridge] MXW01 connection error: {e}")
                await asyncio.sleep(10)

    def run(self, ip=None, mac=None, netmask=None, gateway=None):
        self.start_emulator(ip, mac, netmask, gateway)
        self.start_mxw01_worker()
        print("[Bridge] Epson emulator and MXW01 worker started. Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[Bridge] Shutting down...")
            self.running = False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Epson TM-m30 to MXW01 Bridge")
    parser.add_argument("--mxw01-device", default="MXW01", help="MXW01 device name or address")
    parser.add_argument("--intensity", type=int, default=0x5D, help="Print intensity (0-255)")
    parser.add_argument("--print-mode", choices=["monochrome", "monochrome_2", "grayscale"], default="monochrome", help="Print mode")
    parser.add_argument("--ip", default=None, help="Epson emulator IP address")
    parser.add_argument("--mac", default=None, help="Epson emulator MAC address")
    parser.add_argument("--netmask", default=None, help="Epson emulator netmask")
    parser.add_argument("--gateway", default=None, help="Epson emulator gateway")
    parser.add_argument("--eject-before", type=int, default=None, help="Eject paper by specified line count before printing")
    parser.add_argument("--eject-after", type=int, default=None, help="Eject paper by specified line count after printing")
    parser.add_argument("--retract-before", type=int, default=None, help="Retract paper by specified line count before printing")
    parser.add_argument("--retract-after", type=int, default=None, help="Retract paper by specified line count after printing")
    parser.add_argument("--rotate-180", action="store_true", help="Rotate the image 180 degrees before printing")
    args = parser.parse_args()

    bridge = BridgeEpsonToMXW01(
        mxw01_device=args.mxw01_device,
        intensity=args.intensity,
        print_mode=args.print_mode,
        eject_before=args.eject_before,
        eject_after=args.eject_after,
        retract_before=args.retract_before,
        retract_after=args.retract_after,
        rotate_180=args.rotate_180
    )
    bridge.run(ip=args.ip, mac=args.mac, netmask=args.netmask, gateway=args.gateway)
