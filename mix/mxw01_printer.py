#!/usr/bin/env python3
"""
MXW01 Cat Printer Controller

A Python script to control the MXW01 thermal printer via Bluetooth Low Energy (BLE).
Supports printing images with proper protocol handling as documented in the MXW01 protocol spec.

Requirements:
- bleak (pip install bleak)
- Pillow (pip install Pillow)

Usage:
    # Print an image
    python mxw01_printer.py image.png
    
    # Check printer status
    python mxw01_printer.py --status
    
    # Print with custom intensity and mode
    python mxw01_printer.py image.png --intensity 100 --mode grayscale
    
    # Cancel print job
    python mxw01_printer.py --cancel
"""

import asyncio
import struct
import time
from typing import Optional, List, Tuple
import argparse
import sys
from enum import IntEnum

try:
    from bleak import BleakClient, BleakScanner
    from PIL import Image
    # Remove crc8 dependency since we're implementing it manually
except ImportError as e:
    print(f"Required dependency missing: {e}")
    print("Install with: pip install bleak Pillow")
    sys.exit(1)

# BLE Service and Characteristic UUIDs
MAIN_SERVICE_UUID = "0000ae30-0000-1000-8000-00805f9b34fb"
ALT_MAIN_SERVICE_UUID = "0000af30-0000-1000-8000-00805f9b34fb"  # Mac compatibility
CONTROL_CHAR_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"  # AE01 - Control
NOTIFY_CHAR_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"   # AE02 - Notify
DATA_CHAR_UUID = "0000ae03-0000-1000-8000-00805f9b34fb"     # AE03 - Data

# Protocol constants
PREAMBLE = bytes([0x22, 0x21])
FOOTER = 0xFF
PRINTER_WIDTH = 384  # pixels
MIN_PADDING_BYTES = 4320  # minimum buffer size

# Print modes
class PrintMode(IntEnum):
    MONOCHROME = 0x0   # 1bpp black and white
    MONOCHROME_2 = 0x1         # 1bpp black and white with less paper ejection
    GRAYSCALE = 0x2          # Grayscale mode

# Command IDs
CMD_GET_STATUS = 0xA1
CMD_SET_INTENSITY = 0xA2
CMD_PRINT_REQUEST = 0xA9
CMD_PRINT_FLUSH = 0xAD
CMD_PRINT_COMPLETE = 0xAA
CMD_GET_BATTERY = 0xAB
CMD_CANCEL_PRINT = 0xAC
CMD_QUERY_COUNT = 0xA7
CMD_GET_PRINT_TYPE = 0xB0
CMD_EJECT_PAPER = 0xA3  # Assumed from C# code
CMD_RETRACT_PAPER = 0xA4  # Assumed from C# code
CMD_GET_VERSION = 0xB1

class MXW01Printer:
    async def eject_paper(self, line_count: int = 100):
        """Eject paper by specified line count (default 100)"""
        print(f"Ejecting paper: {line_count} lines")
        payload = struct.pack('<H', line_count)
        response = await self._send_command_and_wait(CMD_EJECT_PAPER, payload)
        if response is not None:
            print("Eject command sent successfully")
            return True
        else:
            print("Eject command failed - no response")
            return False

    async def retract_paper(self, line_count: int = 100):
        """Retract paper by specified line count (default 100)"""
        print(f"Retracting paper: {line_count} lines")
        payload = struct.pack('<H', line_count)
        response = await self._send_command_and_wait(CMD_RETRACT_PAPER, payload)
        if response is not None:
            print("Retract command sent successfully")
            return True
        else:
            print("Retract command failed - no response")
            return False

    def __init__(self, device_identifier: str = "MXW01"):
        """
        device_identifier: BLE address/UUID (macOS/Linux) or advertisement name (e.g., "MXW01")
        """
        self.device_identifier = device_identifier
        self.client: Optional[BleakClient] = None
        self.notification_received = asyncio.Event()
        self.last_notification = None
        self.main_service_uuid = MAIN_SERVICE_UUID  # Track which service UUID is being used
        
    async def scan_and_connect(self) -> bool:
        """Connect to the printer by BLE address/UUID or by scanning for advertisement name."""
        import re
        device_id = self.device_identifier
        # Heuristics: if device_id looks like a MAC address or UUID, connect directly
        is_mac = re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", device_id)
        is_uuid = re.match(r"^[0-9a-fA-F\-]{16,36}$", device_id)
        if is_mac or is_uuid:
            print(f"Connecting directly to device: {device_id}")
            try:
                self.client = BleakClient(device_id)
                await self.client.connect()
                print("Connected to printer")
                # Check for services (try both UUIDs)
                services = await self.client.get_services()
                main_service = None
                for service in services:
                    if service.uuid.lower() in [MAIN_SERVICE_UUID.lower(), ALT_MAIN_SERVICE_UUID.lower()]:
                        main_service = service
                        self.main_service_uuid = service.uuid
                        break
                if not main_service:
                    print("Main service not found")
                    return False
                print(f"Using service: {main_service.uuid}")
                await self.client.start_notify(NOTIFY_CHAR_UUID, self._notification_handler)
                print("Notifications enabled")
                return True
            except Exception as e:
                print(f"Connection failed: {e}")
                return False
        else:
            # Scan for device by name (default behavior)
            print(f"Scanning for device with name containing: {device_id} ...")
            devices = await BleakScanner.discover(timeout=30.0)
            printer_device = None
            for device in devices:
                if device.name and device_id.lower() in device.name.lower():
                    printer_device = device
                    break
            if not printer_device:
                print(f"No printer found with name containing '{device_id}'")
                return False
            print(f"Found printer: {printer_device.name} ({printer_device.address})")
            try:
                self.client = BleakClient(printer_device.address)
                await self.client.connect()
                print("Connected to printer")
                # Check for services (try both UUIDs)
                services = await self.client.get_services()
                main_service = None
                for service in services:
                    if service.uuid.lower() in [MAIN_SERVICE_UUID.lower(), ALT_MAIN_SERVICE_UUID.lower()]:
                        main_service = service
                        self.main_service_uuid = service.uuid
                        break
                if not main_service:
                    print("Main service not found")
                    return False
                print(f"Using service: {main_service.uuid}")
                await self.client.start_notify(NOTIFY_CHAR_UUID, self._notification_handler)
                print("Notifications enabled")
                return True
            except Exception as e:
                print(f"Connection failed: {e}")
                return False
    
    def _notification_handler(self, sender, data):
        """Handle notifications from the printer"""
        self.last_notification = data
        self.notification_received.set()
    
    async def disconnect(self):
        """Disconnect from the printer"""
        if self.client and self.client.is_connected:
            await self.client.stop_notify(NOTIFY_CHAR_UUID)
            await self.client.disconnect()
            print("Disconnected from printer")
    
    def _calculate_crc8(self, data: bytes) -> int:
        """Calculate CRC8 checksum using DALLAS-MAXIM algorithm"""
        # CRC-8 DALLAS-MAXIM: polynomial 0x07, init 0x00, no reflection, no XOR
        crc = 0x00
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x07
                else:
                    crc <<= 1
                crc &= 0xFF
        return crc
    
    def _build_command(self, cmd_id: int, payload: bytes) -> bytes:
        """Build a command packet for the printer"""
        length = len(payload)
        length_bytes = struct.pack('<H', length)  # Little endian
        
        packet = PREAMBLE + bytes([cmd_id, 0x00]) + length_bytes + payload
        crc = self._calculate_crc8(payload)
        packet += bytes([crc, FOOTER])
        
        return packet
    
    async def _send_command_and_wait(self, cmd_id: int, payload: bytes, timeout: float = 5.0) -> Optional[bytes]:
        """Send a command and wait for response"""
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Not connected to printer")
        
        command = self._build_command(cmd_id, payload)
        print(f"Sending command {cmd_id:02X}: {command.hex()}")
        
        self.notification_received.clear()
        
        await self.client.write_gatt_char(CONTROL_CHAR_UUID, command, response=False)
        
        try:
            await asyncio.wait_for(self.notification_received.wait(), timeout=timeout)
            print(f"Received response: {self.last_notification.hex() if self.last_notification else 'None'}")
            return self.last_notification
        except asyncio.TimeoutError:
            print(f"Timeout waiting for response to command {cmd_id:02X}")
            return None
    
    async def get_status(self) -> Optional[dict]:
        """Get printer status"""
        response = await self._send_command_and_wait(CMD_GET_STATUS, bytes([0x00]))
        if not response or len(response) < 8:
            return None
        
        # Parse status response
        status = {
            'raw': response.hex(),
            'status_code': response[6] if len(response) > 6 else 0,
            'battery': response[9] if len(response) > 9 else 0,
            'temperature': response[10] if len(response) > 10 else 0,
            'overall_status': response[12] if len(response) > 12 else 0,
            'error_code': response[13] if len(response) > 13 else 0
        }
        
        return status

    async def get_battery_level(self) -> Optional[int]:
        """Get battery level"""
        response = await self._send_command_and_wait(CMD_GET_BATTERY, bytes([0x00]))
        if not response or len(response) < 7:
            return None
        return response[6]

    async def get_version(self) -> Optional[dict]:
        """Get firmware version and printer type"""
        response = await self._send_command_and_wait(CMD_GET_VERSION, bytes([0x00]))
        if not response or len(response) < 8:
            return None
        
        # Parse based on protocol: `version_utf8(N)`, unknown, type byte
        try:
            # Extract data length from the response (little endian at bytes 4-5)
            data_length = struct.unpack('<H', response[4:6])[0]
            
            # Payload starts at byte 6
            payload = response[6:6+data_length]
            
            # The payload structure is: version_utf8(N), unknown_byte, type_byte
            # So the type byte is the last byte of the payload
            type_byte = payload[-1] if len(payload) > 0 else 0
            
            # Unknown byte is second to last
            unknown_byte = payload[-2] if len(payload) > 1 else 0
            
            # Version string is everything except the last 2 bytes
            version_bytes = payload[:-2] if len(payload) > 2 else payload
            version_str = version_bytes.decode('utf-8', errors='ignore').strip('\x00')
            
            # Decode type according to C# implementation
            type_descriptions = {
                0x32: "gaoya (High pressure/voltage/density?)",
                0x31: "diya (Low pressure/voltage/density?)",
            }
            type_desc = type_descriptions.get(type_byte, "weishibie (???)")
            
            return {
                'raw': response.hex(),
                'version': version_str,
                'type_byte': type_byte,
                'type_description': type_desc,
                'unknown_byte': unknown_byte,
                'data_length': data_length
            }
        except Exception as e:
            return {
                'raw': response.hex(),
                'version': 'Parse Error',
                'type_byte': 0,
                'type_description': 'Unknown',
                'error': str(e)
            }

    async def get_print_type(self) -> Optional[dict]:
        """Get printer type information"""
        response = await self._send_command_and_wait(CMD_GET_PRINT_TYPE, bytes([0x00]))
        if not response or len(response) < 7:
            return None
        
        type_byte = response[6]
        type_descriptions = {
            0x01: "Type 1",
            0x31: "diya (Low pressure/voltage/density?)",
            0x32: "gaoya (High pressure/voltage/density?)",
            0xFF: "Type FF"
        }
        
        return {
            'raw': response.hex(),
            'type_byte': type_byte,
            'type_description': type_descriptions.get(type_byte, f"Unknown ({type_byte:02X})")
        }

    async def cancel_print(self) -> bool:
        """Cancel ongoing print job"""
        try:
            print("Attempting to cancel print job...")
            response = await self._send_command_and_wait(CMD_CANCEL_PRINT, bytes([0x00]))
            if response is not None:
                print("Cancel command sent successfully")
                return True
            else:
                print("Cancel command failed - no response")
                return False
        except Exception as e:
            print(f"Cancel print failed: {e}")
            return False

    async def query_count(self) -> Optional[dict]:
        """Query count information (purpose unknown)"""
        response = await self._send_command_and_wait(CMD_QUERY_COUNT, bytes([0x00]))
        if not response:
            return None
        
        return {
            'raw': response.hex(),
            'data': response[6:] if len(response) > 6 else b''
        }

    def _format_status_display(self, status: dict) -> str:
        """Format status information for display (expanded mapping)"""
        status_codes = {
            0: "Standby",
            1: "Printing",
            2: "Feeding paper",
            3: "Ejecting paper",
        }
        # CatPrinter.cs also uses error code 0x9 for 'No paper', 0x4 for 'Overheated', 0x8 for 'Low battery'
        error_codes = {
            0: "No Error",
            1: "No Paper",
            4: "Overheated",
            8: "Low Battery",
            9: "No Paper",
        }
        # Add more error codes if observed
        status_code = status.get('status_code', -1)
        error_code = status.get('error_code', -1)
        status_name = status_codes.get(status_code, f"Unknown ({status_code})")
        error_name = error_codes.get(error_code, f"Unknown Error ({error_code})")
        overall_status = "OK" if status.get('overall_status', 1) == 0 else "ERROR"
        # Add more details for troubleshooting
        details = []
        details.append(f"Printer Status:")
        details.append(f"  Overall Status: {overall_status}")
        details.append(f"  Current State: {status_name} (code: {status_code})")
        battery_val = status.get('battery', 0)
        details.append(f"  Battery Level: {battery_val}/100 ({battery_val:.1f}%)")
        details.append(f"  Temperature: {status.get('temperature', 0)}")
        details.append(f"  Error: {error_name} (code: {error_code})")
        details.append(f"  Raw Data: {status.get('raw', '')}")
        return "\n".join(details)

    async def print_status(self):
        """Print comprehensive printer status"""
        print("Fetching printer status...")
        
        # Get main status
        status = await self.get_status()
        if status:
            print(self._format_status_display(status))
        else:
            print("Failed to get printer status")
            return False
        
        # Get battery level (separate command)
        print("\nFetching battery level...")
        battery = await self.get_battery_level()
        if battery is not None:
            print(f"Battery Level (detailed): {battery}/255 ({battery/255*100:.1f}%)")
        else:
            print("Failed to get battery level")
        
        # Get version info
        print("\nFetching version information...")
        version = await self.get_version()
        if version:
            print(f"Firmware Version: {version['version']}")
            print(f"Print Type: {version['type_description']}")
            print(f"Type Byte: 0x{version['type_byte']:02X}")
            print(f"Unknown Byte: 0x{version.get('unknown_byte', 0):02X}")
            print(f"Data Length: {version.get('data_length', 'Unknown')}")
            print(f"Raw Version Data: {version['raw']}")
            if 'error' in version:
                print(f"Parse Error: {version['error']}")
        else:
            print("Failed to get version information")
        
        # Get print type info
        print("\nFetching print type information...")
        print_type = await self.get_print_type()
        if print_type:
            print(f"Print Type: {print_type['type_description']}")
            print(f"Type Byte: 0x{print_type['type_byte']:02X}")
            print(f"Raw Type Data: {print_type['raw']}")
        else:
            print("Failed to get print type information")
        
        # Query count (purpose unknown)
        print("\nQuerying count information...")
        count_info = await self.query_count()
        if count_info:
            print(f"Count Query Response: {count_info['raw']}")
            if count_info['data']:
                print(f"Count Data: {count_info['data'].hex()}")
        else:
            print("Failed to get count information")
        
        return True
    
    async def set_intensity(self, intensity: int = 0x5D):
        """Set print intensity (0x00-0xFF, 0x5D is recommended)"""
        if not 0 <= intensity <= 255:
            raise ValueError("Intensity must be between 0 and 255")
        
        print(f"Setting intensity to {intensity} (0x{intensity:02X})")
        
        # Try sending the command without waiting for response first
        command = self._build_command(CMD_SET_INTENSITY, bytes([intensity]))
        print(f"Intensity command: {command.hex()}")
        
        try:
            await self.client.write_gatt_char(CONTROL_CHAR_UUID, command, response=False)
            # Give it a moment to process
            await asyncio.sleep(0.1)
            print("Intensity command sent successfully")
            return True
        except Exception as e:
            print(f"Failed to send intensity command: {e}")
            return False
    
    async def print_image(self, image_path: str, intensity: int = 0x5D, mode: PrintMode = PrintMode.MONOCHROME, rotate_180: bool = False):
        """Print an image file"""
        print(f"Processing image: {image_path}")
        print(f"Print mode: {mode.name} (0x{mode.value:02X})")
        
        # Load and process image
        image_data = self._prepare_image(image_path, mode, rotate_180=rotate_180)
        if not image_data:
            return False
        
        # Set print intensity (some printers don't respond to this command)
        print(f"Setting intensity to {intensity}")
        await self.set_intensity(intensity)  # Don't fail if this doesn't work
        
        # Check status
        print("Checking printer status...")
        status = await self.get_status()
        if not status:
            print("Failed to get printer status")
            return False
        
        if status['overall_status'] != 0:
            error_codes = {1: "No Paper", 4: "Overheated", 8: "Low Battery", 9: "No Paper"}
            error_msg = error_codes.get(status['error_code'], f"Unknown error {status['error_code']}")
            print(f"Printer error: {error_msg}")
            return False
        
        print(f"Printer ready - Battery: {status['battery']}, Temperature: {status['temperature']}")
        
        # Send print request
        line_count = len(image_data) // self._get_bytes_per_line(mode)
        print(f"Sending print request for {line_count} lines...")
        
        print_payload = struct.pack('<H', line_count) + bytes([0x30, mode.value])
        response = await self._send_command_and_wait(CMD_PRINT_REQUEST, print_payload)
        
        if not response or len(response) < 7 or response[6] != 0x00:
            print("Print request rejected")
            return False
        
        print("Print request accepted, sending image data...")
        
        # Send image data in chunks
        chunk_size = 100  # bytes per chunk
        for i in range(0, len(image_data), chunk_size):
            chunk = image_data[i:i + chunk_size]
            await self.client.write_gatt_char(DATA_CHAR_UUID, chunk, response=False)
            await asyncio.sleep(0.02)  # Small delay to avoid overwhelming the printer
            
            if i % 1000 == 0:
                print(f"Sent {i}/{len(image_data)} bytes ({i/len(image_data)*100:.1f}%)")
        
        print("Image data sent, flushing...")
        
        # Flush data
        await self._send_command_and_wait(CMD_PRINT_FLUSH, bytes([0x00]))
        
        # Wait for print completion
        print("Waiting for print completion...")
        self.notification_received.clear()
        
        try:
            await asyncio.wait_for(self.notification_received.wait(), timeout=30.0)
            if self.last_notification and len(self.last_notification) > 2 and self.last_notification[2] == CMD_PRINT_COMPLETE:
                print("Print completed successfully!")
                return True
            else:
                print("Unexpected response while waiting for completion")
                return False
        except asyncio.TimeoutError:
            print("Timeout waiting for print completion")
            return False
    
    def _get_bytes_per_line(self, mode: PrintMode) -> int:
        """Get number of bytes per line for given print mode"""
        if mode == PrintMode.MONOCHROME_2 or mode == PrintMode.MONOCHROME:
            return 48  # 384 pixels / 8 bits per byte = 48 bytes per line
        elif mode == PrintMode.GRAYSCALE:
            return 96  # 384 pixels / 4 bits per pixel = 96 bytes per line (assuming 4bpp)
        else:
            return 48  # Default to monochrome
    
    def _prepare_image(self, image_path: str, mode: PrintMode, rotate_180: bool = False) -> Optional[bytes]:
        """Prepare image for printing"""
        try:
            # Open and convert image
            with Image.open(image_path) as img:
                # Convert to grayscale first
                img = img.convert('L')
                # Resize to printer width while maintaining aspect ratio
                width, height = img.size
                if width != PRINTER_WIDTH:
                    new_height = int(height * PRINTER_WIDTH / width)
                    img = img.resize((PRINTER_WIDTH, new_height), Image.Resampling.LANCZOS)
                # Rotate if requested (after resizing, so orientation is correct)
                if rotate_180:
                    print("Rotating image 180 degrees before printing.")
                    img = img.rotate(180, expand=True)
                print(f"Image prepared: {img.size[0]}x{img.size[1]} pixels")
                # Convert based on mode
                if mode == PrintMode.MONOCHROME_2 or mode == PrintMode.MONOCHROME:
                    return self._prepare_monochrome_image(img)
                elif mode == PrintMode.GRAYSCALE:
                    return self._prepare_grayscale_image(img)
                else:
                    print(f"Unsupported print mode: {mode}")
                    return None
        except Exception as e:
            print(f"Error preparing image: {e}")
            return None
    
    def _prepare_monochrome_image(self, img: Image.Image) -> bytes:
        """Prepare monochrome (1bpp) image data"""
        # Convert to 1-bit (black and white)
        img = img.convert('1')
        
        # Convert to bytes
        image_bytes = bytearray()
        
        for y in range(img.size[1]):
            row_bytes = bytearray()
            for x in range(0, img.size[0], 8):
                byte_val = 0
                for bit in range(8):
                    if x + bit < img.size[0]:
                        pixel = img.getpixel((x + bit, y))
                        # Black pixel = 1, White pixel = 0
                        if pixel == 0:  # PIL uses 0 for black in 1-bit mode
                            byte_val |= (1 << bit)
                row_bytes.append(byte_val)
            
            image_bytes.extend(row_bytes)
        
        # Pad to minimum size
        while len(image_bytes) < MIN_PADDING_BYTES:
            image_bytes.append(0x00)
        
        print(f"Monochrome image data prepared: {len(image_bytes)} bytes")
        return bytes(image_bytes)
    
    def _prepare_grayscale_image(self, img: Image.Image) -> bytes:
        """Prepare grayscale (4bpp) image data - EXPERIMENTAL"""
        # Keep as grayscale
        image_bytes = bytearray()
        
        for y in range(img.size[1]):
            row_bytes = bytearray()
            for x in range(0, img.size[0], 2):
                # Pack 2 pixels into 1 byte (4 bits each)
                pixel1 = img.getpixel((x, y)) if x < img.size[0] else 255
                pixel2 = img.getpixel((x + 1, y)) if x + 1 < img.size[0] else 255
                
                # Convert 8-bit grayscale to 4-bit (0-15)
                # Invert so 0=white, 15=black for thermal printer
                gray1 = 15 - (pixel1 >> 4)
                gray2 = 15 - (pixel2 >> 4)
                
                # Pack into byte: high nibble = first pixel, low nibble = second pixel
                byte_val = (gray1 << 4) | gray2
                row_bytes.append(byte_val)
            
            image_bytes.extend(row_bytes)
        
        # Pad to minimum size
        while len(image_bytes) < MIN_PADDING_BYTES:
            image_bytes.append(0x00)
        
        print(f"Grayscale image data prepared: {len(image_bytes)} bytes")
        return bytes(image_bytes)

async def main():


    parser = argparse.ArgumentParser(description="Print images on MXW01 thermal printer")
    parser.add_argument("image", nargs='?', help="Path to image file to print")
    parser.add_argument("--intensity", type=int, default=0x5D, help="Print intensity (0-255, default: 93)")
    parser.add_argument("--device", default="MXW01", help="Device name to search for")
    parser.add_argument("--status", action="store_true", help="Check printer status only")
    parser.add_argument("--cancel", action="store_true", help="Cancel ongoing print job")
    parser.add_argument("--mode", choices=['monochrome', 'monochrome_2', 'grayscale'], 
                       default='monochrome', help="Print mode (default: monochrome)")
    parser.add_argument("--eject", type=int, metavar="LINES", help="Eject paper by specified line count")
    parser.add_argument("--retract", type=int, metavar="LINES", help="Retract paper by specified line count")
    parser.add_argument("--eject-after", type=int, metavar="LINES", help="Eject paper by specified line count after printing")
    parser.add_argument("--retract-after", type=int, metavar="LINES", help="Retract paper by specified line count after printing")
    parser.add_argument("--eject-before", type=int, metavar="LINES", help="Eject paper by specified line count before printing")
    parser.add_argument("--retract-before", type=int, metavar="LINES", help="Retract paper by specified line count before printing")
    parser.add_argument("--connected-mode", action="store_true", help="Keep connection open for multiple commands interactively")
    parser.add_argument("--rotate-180", action="store_true", help="Rotate the image 180 degrees before printing")

    args = parser.parse_args()

    # Map mode string to enum
    mode_map = {
        'monochrome': PrintMode.MONOCHROME,
        'monochrome_2': PrintMode.MONOCHROME_2,
        'grayscale': PrintMode.GRAYSCALE
    }
    print_mode = mode_map[args.mode]


    # Validate arguments (skip for connected mode)
    if not args.connected_mode and not (args.status or args.cancel or args.image or args.eject or args.retract):
        parser.error("Must provide either an image file, --status, --cancel, --eject, --retract, or --connected-mode flag")

    printer = MXW01Printer(args.device)


    try:
        if not await printer.scan_and_connect():
            return 1

        if args.connected_mode:
            print("\nConnected mode: Type 'help' for commands. Type 'quit' to exit.")
            while True:
                try:
                    user_input = input("mxw01> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting connected mode.")
                    break
                if not user_input:
                    continue
                cmd, *cmd_args = user_input.split()
                if cmd in ("quit", "exit"): 
                    break
                elif cmd == "help":
                    print("""
Available commands:
  print <image_path> [--intensity N] [--mode MODE] [--eject-before N] [--retract-before N] [--eject-after N] [--retract-after N]   Print an image (optionally queue eject/retract before/after)
  status                                             Show printer status
  eject <lines>                                      Eject paper by line count
  retract <lines>                                    Retract paper by line count
  cancel                                             Cancel ongoing print job
  quit/exit                                          Exit connected mode
  help                                               Show this help
Modes: monochrome, monochrome_2, grayscale
                    """)
                elif cmd == "print" and cmd_args:
                    image_path = cmd_args[0]
                    # Parse optional args
                    intensity = args.intensity
                    mode = print_mode
                    eject_after = None
                    retract_after = None
                    eject_before = None
                    retract_before = None
                    rotate_180 = False
                    i = 1
                    while i < len(cmd_args):
                        arg = cmd_args[i]
                        if arg == "--intensity" and i+1 < len(cmd_args):
                            try:
                                intensity = int(cmd_args[i+1])
                            except Exception:
                                print("Invalid intensity value.")
                            i += 2
                            continue
                        if arg == "--mode" and i+1 < len(cmd_args):
                            m = cmd_args[i+1]
                            if m in mode_map:
                                mode = mode_map[m]
                            else:
                                print(f"Unknown mode: {m}")
                            i += 2
                            continue
                        if arg == "--eject-after" and i+1 < len(cmd_args):
                            try:
                                eject_after = int(cmd_args[i+1])
                            except Exception:
                                print("Invalid eject-after value.")
                            i += 2
                            continue
                        if arg == "--retract-after" and i+1 < len(cmd_args):
                            try:
                                retract_after = int(cmd_args[i+1])
                            except Exception:
                                print("Invalid retract-after value.")
                            i += 2
                            continue
                        if arg == "--eject-before" and i+1 < len(cmd_args):
                            try:
                                eject_before = int(cmd_args[i+1])
                            except Exception:
                                print("Invalid eject-before value.")
                            i += 2
                            continue
                        if arg == "--retract-before" and i+1 < len(cmd_args):
                            try:
                                retract_before = int(cmd_args[i+1])
                            except Exception:
                                print("Invalid retract-before value.")
                            i += 2
                            continue
                        if arg == "--rotate-180":
                            rotate_180 = True
                            i += 1
                            continue
                        i += 1
                    # Execute before actions
                    if eject_before is not None:
                        await printer.eject_paper(eject_before)
                    if retract_before is not None:
                        await printer.retract_paper(retract_before)
                    # Wait for printer to be ready after paper movement
                    if eject_before is not None or retract_before is not None:
                        print("Waiting for printer to become ready after paper movement...")
                        for _ in range(60):  # Wait up to ~30 seconds
                            status = await printer.get_status()
                            if status:
                                print(f"  Status: overall_status={status.get('overall_status')}, status_code={status.get('status_code')}")
                                if status.get('overall_status', 1) == 0 and status.get('status_code', 1) == 0:
                                    break
                            await asyncio.sleep(0.5)
                        else:
                            print("Warning: Printer did not become ready after paper movement.")
                    success = await printer.print_image(image_path, intensity, mode, rotate_180=rotate_180)
                    if success:
                        print("Print job completed!")
                        if eject_after is not None:
                            await printer.eject_paper(eject_after)
                        if retract_after is not None:
                            await printer.retract_paper(retract_after)
                    else:
                        print("Print job failed!")
                elif cmd == "status":
                    await printer.print_status()
                elif cmd == "eject" and cmd_args:
                    try:
                        lines = int(cmd_args[0])
                        await printer.eject_paper(lines)
                    except Exception:
                        print("Usage: eject <lines>")
                elif cmd == "retract" and cmd_args:
                    try:
                        lines = int(cmd_args[0])
                        await printer.retract_paper(lines)
                    except Exception:
                        print("Usage: retract <lines>")
                elif cmd == "cancel":
                    await printer.cancel_print()
                else:
                    print("Unknown command. Type 'help' for available commands.")
            # End of connected mode
            return 0

        # Non-connected mode (single command)
        if args.status:
            # Status check mode
            success = await printer.print_status()
            return 0 if success else 1
        elif args.cancel:
            # Cancel print mode
            success = await printer.cancel_print()
            return 0 if success else 1
        elif args.eject is not None:
            # Eject paper
            success = await printer.eject_paper(args.eject)
            return 0 if success else 1
        elif args.retract is not None:
            # Retract paper
            success = await printer.retract_paper(args.retract)
            return 0 if success else 1
        else:
            # Print mode with optional queueing (before/after)
            if args.eject_before is not None:
                await printer.eject_paper(args.eject_before)
            if args.retract_before is not None:
                await printer.retract_paper(args.retract_before)
            # Wait for printer to be ready after paper movement
            if args.eject_before is not None or args.retract_before is not None:
                print("Waiting for printer to become ready after paper movement...")
                for _ in range(60):  # Wait up to ~30 seconds
                    status = await printer.get_status()
                    if status:
                        print(f"  Status: overall_status={status.get('overall_status')}, status_code={status.get('status_code')}")
                        if status.get('overall_status', 1) == 0 and status.get('status_code', 1) == 0:
                            break
                    await asyncio.sleep(0.5)
                else:
                    print("Warning: Printer did not become ready after paper movement.")
            success = await printer.print_image(args.image, args.intensity, print_mode, rotate_180=args.rotate_180)
            if success:
                print("Print job completed successfully!")
                if args.eject_after is not None:
                    await printer.eject_paper(args.eject_after)
                if args.retract_after is not None:
                    await printer.retract_paper(args.retract_after)
                return 0
            else:
                print("Print job failed!")
                return 1

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        # Always disconnect on exit, even in connected mode
        await printer.disconnect()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))