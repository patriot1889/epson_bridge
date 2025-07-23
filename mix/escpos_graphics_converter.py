import struct
from typing import List, Optional, NamedTuple
from PIL import Image
import os

class BitmapImage(NamedTuple):
    height: int
    width: int
    bitmap: bytes

# ESC/POS Graphics Large Data Command
ESCPOS_GRAPHICS_LARGE_DATA_CMD = "1d384c"

def parse_graphics_data_blocks(graphics_hexdata_blocks: List[str]) -> List[Optional[BitmapImage]]:
    """Parse graphics data blocks and extract bitmap images."""
    bitmap_images = []
    
    for index, graphics_hexdata_block in enumerate(graphics_hexdata_blocks):
        workable_graphics_hexdata_block = graphics_hexdata_block
        
        # Extract datasize indicators (4 bytes)
        datasize_hex = workable_graphics_hexdata_block[:8]
        datasize_indicators = [int(datasize_hex[i:i+2], 16) for i in range(0, 8, 2)]
        workable_graphics_hexdata_block = workable_graphics_hexdata_block[8:]
        
        # Extract a1 = ?, a2 = 0x70 = StoreRasterFmtDataToPrintBufferGraphicsSubCmd (2 bytes)
        a1_hex = workable_graphics_hexdata_block[:2]
        a2_hex = workable_graphics_hexdata_block[2:4]
        workable_graphics_hexdata_block = workable_graphics_hexdata_block[4:]
        
        # Pass over filler (4 bytes)
        workable_graphics_hexdata_block = workable_graphics_hexdata_block[8:]
        
        # Extract dimensions indicators (4 bytes)
        dimensions_hex = workable_graphics_hexdata_block[:8]
        dimensions_indicators = [int(dimensions_hex[i:i+2], 16) for i in range(0, 8, 2)]
        workable_graphics_hexdata_block = workable_graphics_hexdata_block[8:]
        
        # Confirm function is StoreRasterFmtDataToPrintBufferGraphicsSubCmd
        if a2_hex != "70":
            bitmap_images.append(None)
            continue
        
        # Calculate datasize (Not used)
        d1, d2, d3, d4 = datasize_indicators
        datasize = (d1 + (d2 * 256) + (d3 * 65536) + (d4 * 16777216)) - 2
        
        # Calculate width and height
        x1, x2, y1, y2 = dimensions_indicators
        width = x1 + (x2 * 256)
        height = y1 + (y2 * 256)
        
        # Extract data
        bitmap_data_length = ((width * height) // 8) * 2
        graphics_data = workable_graphics_hexdata_block[:bitmap_data_length]
        
        # Convert to bytes
        bitmap = bytes.fromhex(graphics_data)
        
        # Logging
        print(f"\n[BLOCK #{index}]")
        print(f"\tDatasize indicators (d1, d2, d3, d4): {datasize_indicators}")
        print(f"\tDatasize: {datasize}")
        print(f"\tDimensions indicators (x1, x2, y1, y2): {dimensions_indicators}")
        print(f"\tDimensions (w, h): ({width}, {height})")
        print(f"\tBitmap data length: {len(bitmap)}")
        
        bitmap_images.append(BitmapImage(
            width=width,
            height=height,
            bitmap=bitmap
        ))
    
    return bitmap_images

def get_escpos_graphics(hexdata: str) -> Optional[List[Optional[BitmapImage]]]:
    """Extract ESC/POS graphics from hex data."""
    # Look for graphics commands
    graphics_large_data_blocks = hexdata.split(ESCPOS_GRAPHICS_LARGE_DATA_CMD)
    
    if len(graphics_large_data_blocks) <= 1:
        return None
    
    # Remove first (useless) item in array
    graphics_large_data_blocks.pop(0)
    
    bitmap_images = parse_graphics_data_blocks(graphics_large_data_blocks)
    return bitmap_images

def bitmap_image_to_pbm(bitmap_image: BitmapImage) -> bytes:
    """Convert bitmap image to PBM format."""
    header = f"P4\n{bitmap_image.width} {bitmap_image.height}\n".encode('ascii')
    return header + bitmap_image.bitmap

def merge_bitmap_images(bitmap_images: List[BitmapImage]) -> BitmapImage:
    """Merge multiple bitmap images vertically."""
    if not bitmap_images:
        raise ValueError("No bitmap images to merge")
    
    # Using first width
    merged_width = bitmap_images[0].width
    merged_height = sum(img.height for img in bitmap_images)
    merged_bitmap = b''.join(img.bitmap for img in bitmap_images)
    
    return BitmapImage(
        width=merged_width,
        height=merged_height,
        bitmap=merged_bitmap
    )

def pbm_to_png(pbm_data: bytes, output_path: str) -> bool:
    """Convert PBM data to PNG using PIL."""
    try:
        # Parse PBM header
        lines = pbm_data.split(b'\n')
        if lines[0] != b'P4':
            raise ValueError("Not a valid PBM P4 file")
        
        # Find dimensions line (skip comments)
        dimensions_line = None
        header_end = 0
        for i, line in enumerate(lines[1:], 1):
            if not line.startswith(b'#'):
                dimensions_line = line
                header_end = len(b'\n'.join(lines[:i+1])) + 1
                break
        
        if not dimensions_line:
            raise ValueError("Could not find dimensions in PBM header")
        
        width, height = map(int, dimensions_line.decode('ascii').split())
        
        # Extract bitmap data
        bitmap_data = pbm_data[header_end:]
        
        print(f"DEBUG: Converting bitmap - Width: {width}, Height: {height}, Data length: {len(bitmap_data)}")
        
        # Method 1: Manual bit-by-bit conversion (most reliable)
        try:
            # Create a list to store pixel values
            pixels = []
            
            for byte_val in bitmap_data:
                # Extract 8 bits from each byte (MSB first)
                for bit_pos in range(7, -1, -1):
                    bit_val = (byte_val >> bit_pos) & 1
                    # ESC/POS typically uses 1=black, 0=white
                    pixels.append(255 if bit_val == 0 else 0)  # 0=black, 255=white
            
            # Trim to exact dimensions (in case of padding)
            pixels = pixels[:width * height]
            
            # Create PIL image from pixel data
            img = Image.new('L', (width, height))
            img.putdata(pixels)
            
            # Save as PNG
            img.save(output_path, 'PNG')
            print(f"Image saved using manual bit conversion")
            return True
            
        except Exception as e:
            print(f"Manual conversion failed: {e}")
        
        # Method 2: Try different PIL raw modes
        raw_modes = ['1;I', '1;0', '1;IL', '1;IR']
        
        for mode in raw_modes:
            try:
                img = Image.frombytes('1', (width, height), bitmap_data, 'raw', mode)
                # Convert to RGB for better compatibility
                img = img.convert('RGB')
                img.save(output_path, 'PNG')
                print(f"Image saved using PIL raw mode: {mode}")
                return True
            except Exception as e:
                print(f"Failed with mode {mode}: {e}")
                continue
            
        # Method 3: Try with bit inversion
        try:
            inverted_data = bytes([~b & 0xFF for b in bitmap_data])
            img = Image.frombytes('1', (width, height), inverted_data, 'raw', '1;I')
            img = img.convert('RGB')
            img.save(output_path, 'PNG')
            print(f"Image saved using inverted bitmap data")
            return True
        except Exception as e:
            print(f"Inverted method failed: {e}")
            
        raise ValueError("Could not create image with any interpretation method")
        
    except Exception as e:
        print(f"Error converting PBM to PNG: {e}")
        return False

def debug_bitmap_data(bitmap_image: BitmapImage, sample_rows: int = 5) -> None:
    """Debug bitmap data by showing first few rows as binary."""
    print(f"\nDEBUG: Bitmap Analysis")
    print(f"Dimensions: {bitmap_image.width}x{bitmap_image.height}")
    print(f"Data length: {len(bitmap_image.bitmap)} bytes")
    print(f"Expected length: {(bitmap_image.width * bitmap_image.height) // 8} bytes")
    
    bytes_per_row = bitmap_image.width // 8
    print(f"Bytes per row: {bytes_per_row}")
    
    print(f"\nFirst {sample_rows} rows as binary:")
    for row in range(min(sample_rows, bitmap_image.height)):
        start_byte = row * bytes_per_row
        end_byte = start_byte + bytes_per_row
        row_data = bitmap_image.bitmap[start_byte:end_byte]
        
        binary_str = ''.join(format(byte, '08b') for byte in row_data)
        print(f"Row {row:2d}: {binary_str[:min(64, len(binary_str))]}...")  # Show first 64 bits
        
        # Show visual representation (0=black █, 1=white ░)
        visual = binary_str[:min(64, len(binary_str))].replace('0', '█').replace('1', '░')
        print(f"       {visual}")
    
    # Check if data is all zeros or all ones
    all_zeros = all(b == 0 for b in bitmap_image.bitmap)
    all_ones = all(b == 255 for b in bitmap_image.bitmap)
    
    if all_zeros:
        print("\nWARNING: All bitmap data is zeros (might appear as black)")
    elif all_ones:
        print("\nWARNING: All bitmap data is ones (might appear as white)")
    else:
        print(f"\nData variation: {len(set(bitmap_image.bitmap))} unique byte values")
        # Show some sample byte values
        unique_bytes = sorted(set(bitmap_image.bitmap))[:10]
        print(f"Sample byte values: {unique_bytes}")
        
        # Check for common patterns
        zero_count = bitmap_image.bitmap.count(0)
        ones_count = bitmap_image.bitmap.count(255)
        print(f"Zero bytes: {zero_count}/{len(bitmap_image.bitmap)} ({zero_count/len(bitmap_image.bitmap)*100:.1f}%)")
        print(f"0xFF bytes: {ones_count}/{len(bitmap_image.bitmap)} ({ones_count/len(bitmap_image.bitmap)*100:.1f}%)")

def generate_merged_bitmap_png(hex_escpos_data: str, skip_indices: List[int] = None, output_dir: str = ".", debug: bool = False) -> Optional[str]:
    """Generate merged bitmap PNG from ESC/POS hex data."""
    if skip_indices is None:
        skip_indices = []
    
    try:
        bitmap_images = get_escpos_graphics(hex_escpos_data)
        
        if not bitmap_images:
            print("No graphics data found")
            return None
        
        # Filter out None values and skip specified indices
        valid_bitmap_images = []
        for i, img in enumerate(bitmap_images):
            if img is not None and i not in skip_indices:
                valid_bitmap_images.append(img)
        
        if not valid_bitmap_images:
            print("No valid bitmap images found")
            return None
        
        # Merge bitmap images
        bitmap_images_merged = merge_bitmap_images(valid_bitmap_images)
        
        # Debug bitmap data if requested
        if debug:
            debug_bitmap_data(bitmap_images_merged)
        
        # Convert to PBM
        pbm_data = bitmap_image_to_pbm(bitmap_images_merged)
        
        # Generate output paths
        pbm_path = os.path.join(output_dir, "escpos-print.pbm")
        png_path = os.path.join(output_dir, "escpos-print.png")
        
        # Write PBM file
        with open(pbm_path, 'wb') as f:
            f.write(pbm_data)
        print(f"PBM file written to: {pbm_path}")
        
        # Convert PBM to PNG
        if pbm_to_png(pbm_data, png_path):
            print(f"PNG file written to: {png_path}")
            return png_path
        else:
            print("Failed to convert PBM to PNG")
            return None
            
    except Exception as e:
        print(f"Error generating PNG: {e}")
        return None

def load_hex_data_from_file(file_path: str) -> str:
    """Load hex data from a file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            hex_data = f.read().strip()
        
        # Remove any whitespace, newlines, or common separators
        hex_data = hex_data.replace(' ', '').replace('\n', '').replace('\r', '').replace('-', '').replace(':', '')
        
        # Validate hex data
        if not all(c in '0123456789abcdefABCDEF' for c in hex_data):
            raise ValueError("File contains non-hexadecimal characters")
        
        return hex_data.lower()
    
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found")
        return ""
    except Exception as e:
        print(f"Error reading file: {e}")
        return ""

# Example usage
if __name__ == "__main__":
    import sys
    
    # Check if file path is provided as command line argument
    if len(sys.argv) > 1:
        hex_file_path = sys.argv[1]
    else:
        # Default file path - change this to your hex data file
        hex_file_path = "escpos_hex_data.txt"
    
    # Load hex data from file
    hex_data = load_hex_data_from_file(hex_file_path)
    
    if not hex_data:
        print("No valid hex data found in file")
        sys.exit(1)
    
    print(f"Loaded {len(hex_data)} characters of hex data from {hex_file_path}")
    
    # Generate PNG (with debug enabled)
    output_path = generate_merged_bitmap_png(hex_data, skip_indices=[], output_dir=".", debug=True)
    
    if output_path:
        print(f"Successfully generated PNG: {output_path}")
    else:
        print("Failed to generate PNG")
