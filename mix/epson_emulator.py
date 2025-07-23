#!/usr/bin/env python3
"""
Epson TM-m30 Printer Emulator
Based on reverse engineering by wes4m (https://wes4m.io/posts/epson_rev/)

This script emulates an Epson TM-m30 thermal printer by implementing:
1. ENPC protocol for printer discovery (UDP port 3289)
2. ESC/POS protocol for print job handling (TCP port 9100)
"""

import socket
import threading
import struct
import time
import binascii
import os
from typing import Optional, Dict, Any
import logging
import ipaddress
import netifaces

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_local_ip():
    """Get the local IP address of the machine"""
    try:
        # Try to connect to a remote address to determine local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        # Fallback: try to get from network interfaces
        try:
            import netifaces
            # Get default gateway interface
            gws = netifaces.gateways()
            default_gw = gws['default'][netifaces.AF_INET]
            interface = default_gw[1]
            
            # Get IP from that interface
            addrs = netifaces.ifaddresses(interface)
            return addrs[netifaces.AF_INET][0]['addr']
        except (ImportError, KeyError):
            # Final fallback
            return "127.0.0.1"

def get_network_info(ip: str):
    """Get network information for the given IP"""
    try:
        import netifaces
        
        # Find the interface that has this IP
        for interface in netifaces.interfaces():
            try:
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        if addr_info['addr'] == ip:
                            netmask = addr_info.get('netmask', '255.255.255.0')
                            
                            # Get gateway
                            gws = netifaces.gateways()
                            if 'default' in gws and netifaces.AF_INET in gws['default']:
                                gateway = gws['default'][netifaces.AF_INET][0]
                            else:
                                # Calculate gateway (usually .1 of the network)
                                network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                                gateway = str(network.network_address + 1)
                            
                            return netmask, gateway
            except Exception:
                continue
                
        # Fallback values
        return "255.255.255.0", "192.168.1.1"
        
    except ImportError:
        # If netifaces not available, use defaults
        return "255.255.255.0", "192.168.1.1"

def get_mac_address(ip: str):
    """Get MAC address for the interface with the given IP"""
    try:
        import netifaces
        
        # Find the interface that has this IP
        for interface in netifaces.interfaces():
            try:
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        if addr_info['addr'] == ip:
                            # Get MAC address for this interface
                            if netifaces.AF_LINK in addrs:
                                return addrs[netifaces.AF_LINK][0]['addr']
            except Exception:
                continue
                
        # Fallback
        return "00:11:22:33:44:55"
        
    except ImportError:
        # If netifaces not available, use default
        return "00:11:22:33:44:55"

class ENPCProtocol:
    """Handles ENPC (Epson Network Print Control) protocol"""
    
    # ENPC Query Functions
    BROADCAST_DISCOVERY = "00000000"  # Initial broadcast discovery
    RETRIEVE_STATUS = "03000010"  # Get printer status (ink, ready state, etc)
    RETRIEVE_BASIC_INFO = "03000000"  # Get basic device info like model name
    WHO_IS_HOLDING = "03000017"  # Check which client is holding the printer
    NETWORK_INFO = "00000010"  # Get network configuration info
    
    # ENPC Command Functions
    COMPULSORY_TRANSMISSION = "03000015"  # Enable/disable compulsory transmission mode
    CLEAR_CONNECTION_TIMEOUT_TIMER = "03000016"  # Clear Connection Time-Out Timer
    
    def __init__(self, ip: str, mac: str, netmask: str, gateway: str):
        self.ip = ip
        self.mac = mac
        self.netmask = netmask
        self.gateway = gateway
        self.is_holding = False
        self.holding_ip = "00000000"
        
    def handle_command(self, func_hex: str, data: bytes, client_socket: Optional[socket.socket] = None) -> Optional[bytes]:
        """Handle ENPC command messages (EPSONC) and return appropriate response"""
        
        if func_hex == self.COMPULSORY_TRANSMISSION:
            # Handle compulsory transmission mode
            # Extract data from the request and echo it back
            data_hex = data.hex()[28:] if len(data.hex()) > 28 else ""
            return self.make_enpc_response("c", self.COMPULSORY_TRANSMISSION, "00000001", data_hex)
            
        elif func_hex == self.CLEAR_CONNECTION_TIMEOUT_TIMER:
            # Clear connection timeout timer command
            # Reset the socket timeout if this is a TCP client
            if client_socket:
                client_socket.settimeout(30)  # Reset the 30 second timeout
                logger.info("Connection timeout timer cleared")
            
            # Response format:
            # - Type: Command response ('c')
            # - Function: Same as request (03000016) 
            # - Data length: 1 byte for result
            # - Data: Result code (0x00 = success)
            return self.make_enpc_response("c", self.CLEAR_CONNECTION_TIMEOUT_TIMER, "00000001", "00")
    
    def parse_enpc_message(self, data: bytes) -> Dict[str, str]:
        """Parse ENPC message into components"""
        hex_data = data.hex().upper()
        
        if len(hex_data) < 14:
            return {}
            
        return {
            'qc': hex_data[:12],  # EPSONQ or EPSONq etc.
            'func_hex': hex_data[12:20],
            'data_len_hex': hex_data[20:28],
            'data_hex': hex_data[28:] if len(hex_data) > 28 else ""
        }
    
    def make_enpc_response(self, response_type: str, func_hex: str, data_len_hex: str, data_hex: str) -> bytes:
        """Create ENPC response packet"""
        # Build header as ASCII bytes
        header = f"EPSON{response_type}".encode('ascii')
        # Convert the rest from hex to bytes
        func_bytes = bytes.fromhex(func_hex)
        data_len_bytes = bytes.fromhex(data_len_hex)
        data_bytes = bytes.fromhex(data_hex) if data_hex else b''
        return header + func_bytes + data_len_bytes + data_bytes
    
    def ip_to_hex(self, ip: str) -> str:
        """Convert IP address to hex string"""
        parts = ip.split('.')
        return ''.join(f"{int(part):02x}" for part in parts)
    
    def mac_to_hex(self, mac: str) -> str:
        """Convert MAC address to hex string"""
        return mac.replace(':', '').replace('-', '').lower()
    
    def handle_query(self, func_hex: str) -> Optional[bytes]:
        """Handle ENPC query and return appropriate response"""
        
        if func_hex == self.BROADCAST_DISCOVERY:
            # Initial broadcast discovery response with device capabilities
            response_data = (
                "55422d45454145303833454e534e0000000000000000000000000000000000000001ffff15000200"
                + self.mac_to_hex(self.mac) + "0000000100000001"
            )
            return self.make_enpc_response("q", self.BROADCAST_DISCOVERY, "00000036", response_data)
        
        elif func_hex == self.RETRIEVE_BASIC_INFO:
            # Basic device info response with model name (TM-m30)
            device_name = "TM-m30"
            device_name_hex = device_name.encode('ascii').hex()
            # Pad to full response length
            padding = "00" * (0x85 - len(device_name_hex) // 2 - 5)
            response_data = f"0005010201{device_name_hex}{padding}"
            return self.make_enpc_response("q", self.RETRIEVE_BASIC_INFO, "00000085", response_data)
        
        elif func_hex == self.NETWORK_INFO:
            # Network configuration response with IP, netmask, gateway, and MAC
            ip_hex = self.ip_to_hex(self.ip)
            netmask_hex = self.ip_to_hex(self.netmask)
            gateway_hex = self.ip_to_hex(self.gateway)
            mac_hex = self.mac_to_hex(self.mac)
            
            response_data = f"01{mac_hex}0004{ip_hex}{netmask_hex}{gateway_hex}807c"
            return self.make_enpc_response("q", self.NETWORK_INFO, "00000017", response_data)
        
        elif func_hex == self.RETRIEVE_STATUS:
            # Status response with printer readiness and capabilities
            # Offset 14 (1 byte): Reserved (00)
            # Offset 15 (4 bytes): ASB (1400000f = printer ready)
            # Offset 19 (4 bytes): ASB Ink (ffffffff = ?)
            # Offset 23 (4 bytes): ASB optional (39414000 = ?)
            response_data = "0e1400000fffffffff39404000"
            return self.make_enpc_response("q", self.RETRIEVE_STATUS, "0000000d", response_data)
        
        elif func_hex == self.WHO_IS_HOLDING:
            # Holding status response
            holding_ip = "00000000" if not self.is_holding else self.holding_ip
            return self.make_enpc_response("q", self.WHO_IS_HOLDING, "00000004", holding_ip)
        
        return None

class ESCPOSProtocol:
    """Handles ESC/POS protocol for print jobs"""
    
    def __init__(self, on_print_job_complete=None):
        self.print_job_counter = 1
        self.on_print_job_complete = on_print_job_complete
        self.escpos_data = ""
    
    def handle_command(self, data: bytes, client_socket: socket.socket) -> bool:
        """Handle ESC/POS command and return True if connection should continue"""
        hex_data = data.hex()
        self.escpos_data += hex_data
        
        logger.info(f"ESC/POS Command: {hex_data}")
        
        # Handle specific ESC/POS commands based on reverse engineering
        if hex_data == "100401":  # DLE EOT command
            # Real-time printer status request
            response = bytes.fromhex("16")  # ACK
            client_socket.send(response)
            logger.info("Sent printer status ACK")
        
        elif "1d61ff" in hex_data:  # Enable Automatic Status Back (ASB)
            response = bytes.fromhex("1400000f")
            client_socket.send(response)
            logger.info("Sent ASB response")
        
        elif hex_data == "1b3d011d2845020006031d496e1b3d011d28450200060b":
            # Complex status request
            client_socket.send(bytes.fromhex("3727331f3600"))
            client_socket.send(bytes.fromhex("3d6e00372731311f3000"))
            logger.info("Sent complex status response")
        
        elif hex_data == "10140801031401060208":
            # Status check
            response = bytes.fromhex("372500")
            client_socket.send(response)
            logger.info("Sent status check response")
        
        elif hex_data == "101406040001031401060208":
            # Another status check
            response = bytes.fromhex("375c3000")
            client_socket.send(response)
            logger.info("Sent another status response")
        
        elif "1d28480600" in hex_data:  # Print job queue command
            # Extract print job counter - take all remaining digits after the command
            parts = hex_data.split("1d28480600")
            if len(parts) > 1:
                counter = parts[1][4:]  # Get all digits after the first 4 bytes
                
                # Send ASB response first
                client_socket.send(bytes.fromhex("1400000f"))
                
                # Send print job completion response
                completion_response = f"3722{counter}00"
                client_socket.send(bytes.fromhex(completion_response))
                
                logger.info(f"Print job {counter} completed")
                
                # Call completion callback if provided
                if self.on_print_job_complete:
                    self.on_print_job_complete(self.escpos_data)
                
                # Reset for next job
                self.escpos_data = ""
                
                # Don't increment counter as it's provided by the client
                return False  # Close connection after job completion
        
        return True  # Continue connection

class EpsonTM30Emulator:
    """Main emulator class that combines ENPC and ESC/POS protocols"""
    
    def __init__(self, ip: str = None, mac: str = None, 
                 netmask: str = None, gateway: str = None):
        # Auto-detect network configuration if not provided
        if ip is None:
            ip = get_local_ip()
        
        if mac is None:
            mac = get_mac_address(ip)
        
        if netmask is None or gateway is None:
            auto_netmask, auto_gateway = get_network_info(ip)
            if netmask is None:
                netmask = auto_netmask
            if gateway is None:
                gateway = auto_gateway
        
        self.ip = ip
        self.mac = mac
        self.netmask = netmask
        self.gateway = gateway
        
        self.enpc = ENPCProtocol(ip, mac, netmask, gateway)
        self.escpos = ESCPOSProtocol(self.on_print_job_complete)
        
        self.enpc_socket = None
        self.escpos_socket = None
        self.running = False
        
        # Create output directory for print jobs
        self.output_dir = "print_jobs"
        os.makedirs(self.output_dir, exist_ok=True)
    
    def on_print_job_complete(self, escpos_data: str):
        """Handle completed print job by saving the ESC/POS data"""
        import time
        timestamp = int(time.time())
        job_filename = f"{self.output_dir}/print_job_{timestamp}.escpos"
        with open(job_filename, 'w') as f:
            f.write(escpos_data)
        logger.info(f"Print job saved to {job_filename}")
    
    
    def start_enpc_server(self):
        """Start ENPC discovery server on UDP port 3289"""
        try:
            self.enpc_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.enpc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.enpc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.enpc_socket.bind(('', 3289))
            
            logger.info("ENPC server started on UDP port 3289")
            
            while self.running:
                try:
                    data, addr = self.enpc_socket.recvfrom(1024)
                    logger.info(f"ENPC query from {addr}: {data.hex()}")
                    
                    if data.startswith(b'EPSONQ') or data.startswith(b'EPSONC'):
                        # Parse query or command
                        parsed = self.enpc.parse_enpc_message(data)
                        logger.info(f"Parsed ENPC message: {parsed}")
                        if parsed:
                            if data.startswith(b'EPSONQ'):
                                logger.info(f"QUERY")
                                response = self.enpc.handle_query(parsed['func_hex'])
                            else:  # EPSONC
                                logger.info(f"COMMAND")
                                # Pass client socket if it's a TCP client
                                response = self.enpc.handle_command(parsed['func_hex'], data,
                                    client_socket=self.escpos_socket if parsed['func_hex'] == self.enpc.CLEAR_CONNECTION_TIMEOUT_TIMER else None)
                                
                            if response:
                                self.enpc_socket.sendto(response, addr)
                                logger.info(f"ENPC response sent to {addr}: {response.hex()}")
                            else:
                                logger.warning(f"No ENPC response generated for func_hex: {parsed['func_hex']}")
                        else:
                            logger.warning(f"Failed to parse ENPC message from {addr}: {data.hex()}")
                
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"ENPC server error: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to start ENPC server: {e}")
    
    def start_escpos_server(self):
        """Start ESC/POS print server on TCP port 9100"""
        try:
            self.escpos_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.escpos_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.escpos_socket.bind((self.ip, 9100))
            self.escpos_socket.listen(5)
            
            logger.info(f"ESC/POS server started on {self.ip}:9100")
            
            while self.running:
                try:
                    client_socket, addr = self.escpos_socket.accept()
                    logger.info(f"ESC/POS connection from {addr}")
                    
                    # Set printer as held by this client
                    self.enpc.is_holding = True
                    self.enpc.holding_ip = self.enpc.ip_to_hex(addr[0])
                    
                    # Handle client in separate thread
                    client_thread = threading.Thread(
                        target=self.handle_escpos_client,
                        args=(client_socket, addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                    
                except Exception as e:
                    logger.error(f"ESC/POS server error: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to start ESC/POS server: {e}")
    
    def handle_escpos_client(self, client_socket: socket.socket, addr):
        """Handle individual ESC/POS client connection"""
        try:
            client_socket.settimeout(30)  # 30 second timeout
            
            # Update holding status with client's IP address
            client_ip = addr[0]  # Get IP from addr tuple
            self.enpc.is_holding = True
            self.enpc.holding_ip = self.enpc.ip_to_hex(client_ip)  # Use existing ip_to_hex method
            logger.info(f"Printer now held by {client_ip} (hex: {self.enpc.holding_ip})")
            
            while self.running:
                try:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    
                    # Process ESC/POS command
                    should_continue = self.escpos.handle_command(data, client_socket)
                    if not should_continue:
                        break
                        
                except socket.timeout:
                    logger.info(f"ESC/POS client {addr} timed out")
                    break
                except Exception as e:
                    logger.error(f"Error handling ESC/POS client {addr}: {e}")
                    break
        
        finally:
            client_socket.close()
            # Release printer hold
            self.enpc.is_holding = False
            self.enpc.holding_ip = "00000000"
            logger.info(f"ESC/POS client {client_ip} disconnected, printer released")
    
    def start(self):
        """Start the printer emulator"""
        self.running = True
        
        logger.info(f"Starting Epson TM-m30 emulator")
        logger.info(f"IP: {self.ip}, MAC: {self.mac}")
        logger.info(f"Network: {self.netmask}, Gateway: {self.gateway}")
        
        # Start ENPC server thread
        enpc_thread = threading.Thread(target=self.start_enpc_server)
        enpc_thread.daemon = True
        enpc_thread.start()
        
        # Start ESC/POS server thread
        escpos_thread = threading.Thread(target=self.start_escpos_server)
        escpos_thread.daemon = True
        escpos_thread.start()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down printer emulator")
            self.stop()
    
    def stop(self):
        """Stop the printer emulator"""
        self.running = False
        
        if self.enpc_socket:
            self.enpc_socket.close()
        
        if self.escpos_socket:
            self.escpos_socket.close()
        
        logger.info("Printer emulator stopped")

def main():
    """Main function to run the emulator"""
    import argparse
    
    # Get default values
    default_ip = get_local_ip()
    default_mac = get_mac_address(default_ip)
    default_netmask, default_gateway = get_network_info(default_ip)
    
    parser = argparse.ArgumentParser(description='Epson TM-m30 Printer Emulator')
    parser.add_argument('--ip', default=default_ip, help=f'Printer IP address (default: {default_ip})')
    parser.add_argument('--mac', default=default_mac, help=f'Printer MAC address (default: {default_mac})')
    parser.add_argument('--netmask', default=default_netmask, help=f'Network netmask (default: {default_netmask})')
    parser.add_argument('--gateway', default=default_gateway, help=f'Network gateway (default: {default_gateway})')
    
    args = parser.parse_args()
    
    emulator = EpsonTM30Emulator(
        ip=args.ip,
        mac=args.mac,
        netmask=args.netmask,
        gateway=args.gateway
    )
    
    emulator.start()

if __name__ == '__main__':
    main()
