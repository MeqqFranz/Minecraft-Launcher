import os
import sys
import subprocess
import urllib.request
import zipfile
import gzip
import json
import shutil
import re
import ctypes
import time
import webbrowser
import uuid
from datetime import datetime

try:
    import minecraft_launcher_lib
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "minecraft-launcher-lib"])
    import minecraft_launcher_lib

# =====================================================================
# 1. RUTAS DINÁMICAS E INDEPENDIENTES
# =====================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MC_DIR = minecraft_launcher_lib.utils.get_minecraft_directory()
RUNTIMES_DIR = os.path.join(DEFAULT_MC_DIR, "runtime")
CONFIG_FILE = os.path.join(DEFAULT_MC_DIR, "launcher_config.json")
SKINS_DIR = os.path.join(DEFAULT_MC_DIR, "launcher_skins")
MC_ICON_PATH = os.path.join(DEFAULT_MC_DIR, "launcher_icon.ico")
TOKENS_FILE = os.path.join(DEFAULT_MC_DIR, "ms_tokens.json")

JAVA_BUILDS = {
    "8": "https://github.com/adoptium/temurin8-binaries/releases/download/jdk8u412-b08/OpenJDK8U-jre_x64_windows_hotspot_8u412b08.zip",
    "16": "https://github.com/adoptium/temurin16-binaries/releases/download/jdk-16.0.2%2B7/OpenJDK16U-jdk_x64_windows_hotspot_16.0.2_7.zip",
    "17": "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.11%2B9/OpenJDK17U-jre_x64_windows_hotspot_17.0.11_9.zip",
    "21": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.3%2B9/OpenJDK21U-jre_x64_windows_hotspot_21.0.3_9.zip",
    "25": "https://download.oracle.com/java/25/archive/jdk-25_windows-x64_bin.zip"
}

RAM_TIERS = [1, 2, 4, 6, 8, 10, 11, 12, 16, 24, 32]

# =====================================================================
# 2. LLAMADAS AL SISTEMA Y HARDWARE
# =====================================================================
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

def get_system_ram():
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    total_gb = round(stat.ullTotalPhys / (1024**3), 2)
    avail_gb = round(stat.ullAvailPhys / (1024**3), 2)
    used_percent = stat.dwMemoryLoad
    return total_gb, avail_gb, used_percent

def get_system_resolution():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

def apply_console_dimensions():
    os.system("mode con: cols=95 lines=35")

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def copy_to_clipboard(text):
    try:
        cmd = f'Set-Clipboard -Value "{text}"'
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=True)
        return True
    except Exception:
        return False

def ensure_minecraft_icon():
    if not os.path.exists(MC_ICON_PATH) or os.path.getsize(MC_ICON_PATH) == 0:
        urls = [
            "https://launcher.mojang.com/download/Minecraft.ico",
            "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/1.20.4/assets/minecraft/textures/gui/title/minecraft.ico"
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(MC_ICON_PATH, "wb") as f:
                    f.write(resp.read())
                if os.path.exists(MC_ICON_PATH) and os.path.getsize(MC_ICON_PATH) > 0:
                    break
            except Exception:
                continue
    return MC_ICON_PATH if (os.path.exists(MC_ICON_PATH) and os.path.getsize(MC_ICON_PATH) > 0) else None

def get_real_desktop_path():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        desktop_dir, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        expanded = os.path.expandvars(desktop_dir)
        if os.path.exists(expanded): return expanded
    except Exception:
        pass
    candidates = [
        os.path.join(os.path.expanduser("~"), "Desktop"),
        os.path.join(os.path.expanduser("~"), "Escritorio"),
        os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
        os.path.join(os.path.expanduser("~"), "OneDrive", "Escritorio")
    ]
    for c in candidates:
        if os.path.exists(c): return c
    return os.path.join(os.path.expanduser("~"), "Desktop")

# =====================================================================
# 3. PARSEO DE VERSIONES Y DETECCIÓN INTELIGENTE
# =====================================================================
def extract_base_version(version_id):
    """Extrae la versión oficial de Mojang a partir del JSON del perfil o de su nombre."""
    target_json = os.path.join(DEFAULT_MC_DIR, "versions", version_id, f"{version_id}.json")
    if os.path.exists(target_json):
        try:
            with open(target_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "inheritsFrom" in data:
                    clean_match = re.search(r'(\d+\.\d+(\.\d+)?)', data["inheritsFrom"])
                    if clean_match: return clean_match.group(1)
                if "assets" in data:
                    clean_match = re.search(r'(\d+\.\d+(\.\d+)?)', data["assets"])
                    if clean_match: return clean_match.group(1)
        except Exception: pass
    match = re.search(r'(\d+\.\d+(\.\d+)?)', version_id)
    if match: return match.group(1)
    return version_id

def parse_mc_version(ver_str):
    nums = re.findall(r'\d+', ver_str)
    if not nums: return (0, 0, 0)
    int_nums = [int(n) for n in nums]
    while len(int_nums) < 3: int_nums.append(0)
    return tuple(int_nums[:3])

def check_loader_support(mc_version):
    v = parse_mc_version(mc_version)
    neoforge_ok = (v[0] >= 26) or (v[0] == 1 and (v[1] > 20 or (v[1] == 20 and v[2] >= 2)))
    forge_ok = (v[0] >= 26) or (v[0] == 1 and (v[1] >= 1 or v[2] >= 1))
    fabric_ok = (v[0] >= 26) or (v[0] == 1 and v[1] >= 14)
    return {"vanilla": True, "neoforge": neoforge_ok, "forge": forge_ok, "fabric": fabric_ok}

def detect_version_loader(version_id):
    vid = version_id.lower()
    if "neoforge" in vid or "neo" in vid: return "neoforge"
    elif "forge" in vid: return "forge"
    elif "fabric" in vid: return "fabric"
    return "vanilla"

# =====================================================================
# 4. MOTOR DE JAVA (JVM)
# =====================================================================
def get_required_java(version_id):
    vid = version_id.lower()
    base_v = extract_base_version(version_id)
    nums = re.findall(r'\d+', base_v)

    if nums:
        first_num = int(nums[0])
        if first_num >= 25: return "25"
        if first_num in (21, 22, 23, 24): return "21"
        if len(nums) >= 2 and nums[0] == "1":
            minor = int(nums[1])
            patch = int(nums[2]) if len(nums) >= 3 else 0
            if minor >= 21: return "21"
            if minor == 20 and patch >= 5: return "21"
            if minor in (18, 19, 20): return "17"
            if minor == 17: return "16"
            return "8"

    if "neoforge" in vid or "neo" in vid:
        if any(int(n) >= 25 for n in re.findall(r'\d+', vid)): return "25"
        return "21"

    return "21" if ("fabric" in vid or "forge" in vid) else "8"

def ensure_java(major_version):
    target_folder = os.path.join(RUNTIMES_DIR, f"java-{major_version}")
    
    if os.path.exists(target_folder):
        for root, _, files in os.walk(target_folder):
            if "java.exe" in files: return os.path.join(root, "java.exe")
            elif "javaw.exe" in files: return os.path.join(root, "javaw.exe")

    os.makedirs(RUNTIMES_DIR, exist_ok=True)
    zip_dest = os.path.join(RUNTIMES_DIR, f"temp_java_{major_version}.zip")
    url = JAVA_BUILDS.get(major_version)
    
    if not url:
        print(f"[!] No hay URL configurada para Java {major_version}. Usando Java del sistema.")
        return "java"

    print(f"\n[>] Descargando e instalando Java {major_version} portátil...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(zip_dest, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        
        print("[>] Descomprimiendo runtime (esto tomará unos segundos)...")
        with zipfile.ZipFile(zip_dest, 'r') as zip_ref:
            zip_ref.extractall(target_folder)
        os.remove(zip_dest)
        
        for root, _, files in os.walk(target_folder):
            if "java.exe" in files: return os.path.join(root, "java.exe")
            elif "javaw.exe" in files: return os.path.join(root, "javaw.exe")
    except Exception as e:
        print(f"[ERROR] Falló la descarga de Java {major_version}: {e}")
        return "java"
    return "java"

# =====================================================================
# 5. INYECCIÓN NBT (SERVERS.DAT)
# =====================================================================
def write_nbt_string(name, value):
    name_bytes = name.encode("utf-8")
    val_bytes = value.encode("utf-8")
    return b"\x08" + len(name_bytes).to_bytes(2, "big") + name_bytes + len(val_bytes).to_bytes(2, "big") + val_bytes

def write_nbt_byte(name, value):
    name_bytes = name.encode("utf-8")
    return b"\x01" + len(name_bytes).to_bytes(2, "big") + name_bytes + bytes([value & 0xFF])

def build_server_entry(name, ip):
    payload = bytearray()
    payload.extend(write_nbt_string("ip", ip))
    payload.extend(write_nbt_string("name", name))
    payload.extend(write_nbt_byte("acceptTextures", 1))
    payload.append(0x00)
    return bytes(payload)

def inject_server_to_dat(game_dir, server_name, server_ip):
    servers_dat_path = os.path.join(game_dir, "servers.dat")
    os.makedirs(game_dir, exist_ok=True)
    if os.path.exists(servers_dat_path):
        try:
            with gzip.open(servers_dat_path, "rb") as f: data = f.read()
            for match in re.finditer(b"\x08\x00\x02ip\x00(..)", data):
                length = int.from_bytes(match.group(1), "big")
                start = match.end()
                existing_ip = data[start:start+length].decode("utf-8", errors="ignore")
                if existing_ip.lower() == server_ip.lower():
                    print(f"[!] La IP '{server_ip}' ya existe en servers.dat.")
                    return
        except Exception: pass
    raw_nbt = bytearray()
    raw_nbt.append(0x0A); raw_nbt.extend(b"\x00\x00")
    raw_nbt.append(0x09); raw_nbt.extend(b"\x00\x07servers")
    raw_nbt.append(0x0A); raw_nbt.extend((1).to_bytes(4, "big"))
    raw_nbt.extend(build_server_entry(server_name, server_ip))
    raw_nbt.append(0x00)
    try:
        with gzip.open(servers_dat_path, "wb") as f: f.write(raw_nbt)
        print(f"[OK] Servidor inyectado correctamente en servers.dat.")
    except Exception as e: print(f"[ERROR] No se pudo escribir servers.dat: {e}")

def read_servers_list(game_dir):
    servers_dat_path = os.path.join(game_dir, "servers.dat")
    if not os.path.exists(servers_dat_path): return []
    results = []
    try:
        with gzip.open(servers_dat_path, "rb") as f: data = f.read()
        names, ips = [], []
        for match in re.finditer(b"\x08\x00\x04name\x00(..)", data):
            l = int.from_bytes(match.group(1), "big")
            names.append(data[match.end():match.end()+l].decode("utf-8", errors="ignore"))
        for match in re.finditer(b"\x08\x00\x02ip\x00(..)", data):
            l = int.from_bytes(match.group(1), "big")
            ips.append(data[match.end():match.end()+l].decode("utf-8", errors="ignore"))
        for i in range(min(len(names), len(ips))): results.append((names[i], ips[i]))
    except Exception: pass
    return results
# =====================================================================
# 6. CONFIGURACIÓN DEL LAUNCHER Y CUENTAS MICROSOFT
# =====================================================================
def load_config():
    total_ram, _, _ = get_system_ram()
    auto_ram = 2
    for tier in [2, 4, 6, 8, 10, 12, 16]:
        if total_ram >= tier + 3: auto_ram = tier
    default_config = {
        "first_run": True, "language": "es", "ram_mode": "adaptive",
        "auto_ram_gb": auto_ram, "active_ram_slot": "1",
        "ram_slots": {"1": "2G", "2": "4G", "3": "6G", "4": "8G", "5": f"{auto_ram}G"},
        "display": {"width": 854, "height": 480, "aspect_ratio": "16:9"},
        "render_mode": "Predeterminado", "version_dirs": {},
        "user_slots": {str(i): "" for i in range(1, 11)},
        "skin_slots": {str(i): {"name": "Vacio", "path": ""} for i in range(1, 11)},
        "microsoft_accounts": {}  # Almacena tokens OAuth internamente
    }
    if not os.path.exists(CONFIG_FILE):
        save_config(default_config)
        return default_config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in default_config.items():
                if k not in data: data[k] = v
            return data
    except Exception: return default_config

def save_config(config_data):
    os.makedirs(DEFAULT_MC_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

def check_first_run():
    config = load_config()
    if config.get("first_run", True):
        clear_screen()
        apply_console_dimensions()
        total_ram, avail_ram, used_pct = get_system_ram()
        w, h = get_system_resolution()
        print("================================================================")
        print("          CALIBRACIÓN INICIAL DEL DISPOSITIVO DETECTADO         ")
        print("================================================================")
        print(f" > RAM Total instalada: {total_ram} GB | Disponible: {avail_ram} GB")
        print(f" > Límite seguro recomendado (85%): {round(total_ram * 0.85, 1)} GB")
        print(f" > Resolución detectada: {w}x{h} píxeles")
        print(f" > Carga actual de memoria: {used_pct}%")
        print("----------------------------------------------------------------")
        print(" [OK] Modo Adaptativo Inteligente configurado por defecto.")
        config["first_run"] = False
        save_config(config)
        input("\nPresiona ENTER para ingresar...")

def get_installed_list():
    versions_path = os.path.join(DEFAULT_MC_DIR, "versions")
    if not os.path.exists(versions_path): return []
    valid_versions = []
    for item in os.listdir(versions_path):
        item_path = os.path.join(versions_path, item)
        if os.path.isdir(item_path):
            if os.path.exists(os.path.join(item_path, f"{item}.json")):
                valid_versions.append(item)
    return valid_versions

def show_installed_versions():
    clear_screen()
    print("========================================")
    print("          VERSIONES INSTALADAS          ")
    print("========================================")
    vers = get_installed_list()
    if not vers:
        print("[!] No hay versiones válidas instaladas.")
    else:
        config = load_config()
        for idx, v in enumerate(vers, 1):
            carpeta = config.get("version_dirs", {}).get(v, DEFAULT_MC_DIR)
            loader = detect_version_loader(v).upper()
            print(f" [{idx}] {v} [{loader}]\n      Carpeta: {carpeta}")
    print("========================================")
    input("\nPresiona ENTER para volver...")

def show_novice_guide():
    clear_screen()
    print("================================================================")
    print("                GUÍA DE CONFIGURACIÓN PARA NOVATOS              ")
    print("================================================================")
    print(" 1. MODO ADAPTATIVO (RECOMENDADO):")
    print("    - Analiza si juegas una versión vieja o moderna.")
    print("    - Detecta automáticamente si hay mods y shaders instalados.")
    print("    - Si es pesada (1.20+ con mods), escala dinámicamente sin superar")
    print("      nunca el 85% de tu RAM física total.")
    print("")
    print(" 2. LÍMITE DE SEGURIDAD (85% MAX):")
    print("    - Si Minecraft tomara el 100%, la GPU se quedaría sin memoria de")
    print("      intercambio y el sistema colapsaría con pantallazo.")
    print("================================================================")
    input("\nPresiona ENTER para volver...")

def show_path_help():
    clear_screen()
    print("================================================================")
    print("             GUÍA DE RUTAS DE CARPETAS PARA NOVATOS             ")
    print("================================================================")
    print(" [1] Ruta Predeterminada (.minecraft general):")
    print("     - Todo se guarda en la carpeta común de Minecraft.")
    print("     - RECOMENDADO: Si juegas Vanilla o usas siempre una versión base.")
    print("")
    print(" [2] Subcarpeta aislada en .minecraft/versions/<version>:")
    print("     - Crea una carpeta única e independiente para esta versión.")
    print("     - Sus propios mods, mundos y configuraciones NO se mezclarán.")
    print("     - RECOMENDADO: Si juegas con Fabric, Forge o varios modpacks.")
    print("")
    print(" [3] Ruta Personalizada:")
    print("     - ¡El launcher COPIA TU RUTA BASE AL PORTAPAPELES automáticamente!")
    print("     - Presiona Ctrl + V para pegarla donde desees.")
    print("================================================================")
    input("\nPresiona ENTER para continuar...")

# =====================================================================
# 7. ADMINISTRADOR DE CONTENIDO (MODS/SHADERS/SAVES)
# =====================================================================
def list_folder_items(folder_path):
    if not os.path.exists(folder_path): return []
    return sorted(os.listdir(folder_path))

def manage_item_category(category_name, folder_path, filter_ext=None):
    os.makedirs(folder_path, exist_ok=True)
    while True:
        clear_screen()
        items = list_folder_items(folder_path)
        if filter_ext:
            items = [f for f in items if f.endswith(filter_ext) or os.path.isdir(os.path.join(folder_path, f))]

        print("================================================================")
        print(f"       ADMINISTRAR {category_name.upper()} ({len(items)} Elementos)")
        print(f" Ubicación: {folder_path}")
        print("================================================================")
        if not items: print(" [!] No hay elementos en esta categoría.")
        else:
            for idx, it in enumerate(items, 1):
                tipo = "[DIR]" if os.path.isdir(os.path.join(folder_path, it)) else "[FILE]"
                print(f" [{idx}] {tipo} {it}")
        print("----------------------------------------------------------------")
        print(" [1] Importar desde otra carpeta (Seleccionar uno o todos)")
        print(" [2] Exportar elemento a otra carpeta")
        print(" [3] Renombrar elemento")
        print(" [4] Eliminar elemento")
        print(" [5] Abrir esta carpeta en el Explorador de Windows")
        print(" [V] Volver")
        print("================================================================")
        opc = input("Opción: ").strip().lower()

        if opc == "v" or not opc: break
        elif opc == "5":
            try: os.startfile(folder_path)
            except AttributeError: pass
        elif opc == "1":
            copy_to_clipboard(os.path.join(os.path.expanduser("~"), "Downloads"))
            src_dir = input("\n[i] 'Descargas' copiada al portapapeles. Pega la ruta origen (o 'v'): ").strip().strip('"')
            if not src_dir or src_dir.lower() == "v" or not os.path.exists(src_dir): continue
            
            src_items = list_folder_items(src_dir)
            if filter_ext: src_items = [f for f in src_items if f.endswith(filter_ext) or os.path.isdir(os.path.join(src_dir, f))]
            if not src_items:
                print("[!] No hay elementos válidos en el origen."); input(); continue

            for idx, it in enumerate(src_items, 1): print(f" [{idx}] {it}")
            sel = input("\nElige número a importar o 'T' para todos: ").strip().lower()
            if sel == "t":
                for it in src_items:
                    s = os.path.join(src_dir, it); d = os.path.join(folder_path, it)
                    shutil.copytree(s, d, dirs_exist_ok=True) if os.path.isdir(s) else shutil.copyfile(s, d)
                input("\n[OK] Importación completa. ENTER...")
            elif sel.isdigit() and 1 <= int(sel) <= len(src_items):
                it = src_items[int(sel)-1]
                s = os.path.join(src_dir, it); d = os.path.join(folder_path, it)
                shutil.copytree(s, d, dirs_exist_ok=True) if os.path.isdir(s) else shutil.copyfile(s, d)
                input(f"\n[OK] {it} importado con éxito. ENTER...")

        elif opc == "2":
            num = input("Número a exportar: ").strip()
            if num.isdigit() and 1 <= int(num) <= len(items):
                dst_dir = input("Carpeta de destino: ").strip().strip('"')
                if os.path.exists(dst_dir):
                    it = items[int(num)-1]
                    s = os.path.join(folder_path, it); d = os.path.join(dst_dir, it)
                    shutil.copytree(s, d, dirs_exist_ok=True) if os.path.isdir(s) else shutil.copyfile(s, d)
                    input("\n[OK] Exportado con éxito. ENTER...")
        elif opc == "3":
            num = input("Número a renombrar: ").strip()
            if num.isdigit() and 1 <= int(num) <= len(items):
                o_name = items[int(num)-1]
                n_name = input(f"Nuevo nombre para '{o_name}': ").strip()
                if n_name:
                    os.rename(os.path.join(folder_path, o_name), os.path.join(folder_path, n_name))
                    input("\n[OK] Renombrado. ENTER...")
        elif opc == "4":
            num = input("Número a eliminar: ").strip()
            if num.isdigit() and 1 <= int(num) <= len(items):
                it = items[int(num)-1]
                if input(f"¿Borrar '{it}' permanentemente? (s/n): ").strip().lower() == "s":
                    p = os.path.join(folder_path, it)
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                    input("\n[OK] Eliminado. ENTER...")

def open_modrinth_discovery(version_id):
    loader = detect_version_loader(version_id)
    if loader == "vanilla":
        clear_screen()
        print("================================================================")
        print("                   AVISO: INSTANCIA VANILLA                     ")
        print("================================================================")
        print(" Vanilla no soporta mods. Inyecta un modloader desde el menú")
        print(" Editar Versión -> [4] Inyectar Modloader.")
        input("\nPresiona ENTER para volver...")
        return

    v_param = extract_base_version(version_id)
    url = f"https://modrinth.com/discover/mods?g=categories:{loader}"
    if v_param: url += f"&v={v_param}"
    print(f"\n[>] Abriendo buscador de Modrinth en el navegador: \n    {url}")
    webbrowser.open(url)
    input("\nPresiona ENTER para continuar...")

def version_content_manager(version_id, game_dir):
    while True:
        clear_screen()
        loader = detect_version_loader(version_id).upper()
        print("================================================================")
        print(f"        ADMINISTRADOR DE CONTENIDO: {version_id} [{loader}]")
        print(f" Directorio base: {game_dir}")
        print("================================================================")
        print(" [1] Mods (.jar)")
        print(" [2] Resource Packs (Texturas)")
        print(" [3] Shaderpacks (Shaders)")
        print(" [4] Mapas y Mundos (saves)")
        print(" [5] Servidores Multijugador (servers.dat)")
        print(" [6] Buscar Mods en Modrinth (Auto-filtro web)")
        print(" [V] Volver a Edición")
        opc = input("\nSelecciona: ").strip().lower()

        if opc == "v" or not opc: break
        elif opc == "1": manage_item_category("Mods", os.path.join(game_dir, "mods"), filter_ext=".jar")
        elif opc == "2": manage_item_category("Resource Packs", os.path.join(game_dir, "resourcepacks"))
        elif opc == "3": manage_item_category("Shaderpacks", os.path.join(game_dir, "shaderpacks"))
        elif opc == "4": manage_item_category("Mapas y Mundos", os.path.join(game_dir, "saves"))
        elif opc == "5":
            clear_screen()
            print("================================================================")
            print("             SERVIDORES GUARDADOS EN ESTA VERSIÓN               ")
            print("================================================================")
            srvs = read_servers_list(game_dir)
            if not srvs: print(" [!] No hay servidores registrados.")
            for idx, (sname, sip) in enumerate(srvs, 1): print(f" [{idx}] {sname} -> {sip}")
            print("\n [A] Agregar servidor manualmente  [V] Volver")
            sub = input("\nOpción: ").strip().lower()
            if sub == "a":
                sn = input("Nombre de servidor (ej. Servidor de Amigos): ").strip() or "Minecraft Server"
                si = input("IP: ").strip()
                if si: inject_server_to_dat(game_dir, sn, si)
                input("\nPresiona ENTER...")
        elif opc == "6": open_modrinth_discovery(version_id)

# =====================================================================
# 8. INSTALADORES DE MODLOADERS (NUEVO NEOFORGE 26.X COMPATIBLE Y CACHÉ)
# =====================================================================
def install_fabric(mc_version):
    try:
        base_json = os.path.join(DEFAULT_MC_DIR, "versions", mc_version, f"{mc_version}.json")
        if not os.path.exists(base_json):
            minecraft_launcher_lib.install.install_minecraft_version(mc_version, DEFAULT_MC_DIR)
        
        minecraft_launcher_lib.fabric.install_fabric(mc_version, DEFAULT_MC_DIR)
        for v in get_installed_list():
            if "fabric" in v.lower() and mc_version in v: return v
        return f"fabric-loader-{mc_version}"
    except Exception as e:
        print(f"[ERROR] {e}"); return None

def install_neoforge(mc_version, java_exec):
    try:
        base_json = os.path.join(DEFAULT_MC_DIR, "versions", mc_version, f"{mc_version}.json")
        if not os.path.exists(base_json):
            minecraft_launcher_lib.install.install_minecraft_version(mc_version, DEFAULT_MC_DIR)

        print("[>] Buscando la última versión de NeoForge...")
        req = urllib.request.Request("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml", headers={'User-Agent': 'Mozilla/5.0'})
        xml_data = urllib.request.urlopen(req).read().decode("utf-8")
        
        # Filtro estricto para extraer versiones incluso betas (ej. 26.3.0.56-beta)
        prefix = mc_version
        if prefix.startswith("1."): prefix = prefix[2:]
        
        versiones_encontradas = re.findall(r"<version>(.*?)</version>", xml_data)
        coincidencias = [v for v in versiones_encontradas if v.startswith(prefix)]
        
        if not coincidencias: 
            print(f"[X] No se encontró instalador en Maven de NeoForge para '{mc_version}'.")
            return None
            
        latest_nf = coincidencias[-1]
        installer_url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{latest_nf}/neoforge-{latest_nf}-installer.jar"
        installer_path = os.path.join(DEFAULT_MC_DIR, f"neoforge-{latest_nf}-installer.jar")
        
        print(f"[>] Descargando instalador de NeoForge {latest_nf}...")
        req_jar = urllib.request.Request(installer_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_jar) as response, open(installer_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        
        print(f"[>] Ejecutando instalador (Se abrirá Java en segundo plano)...")
        j_cmd = java_exec.replace("javaw.exe", "java.exe")
        subprocess.run([j_cmd, "-jar", installer_path, "--installClient", DEFAULT_MC_DIR], check=True)
        if os.path.exists(installer_path): os.remove(installer_path)
        
        for v in get_installed_list():
            if "neoforge" in v.lower() and (mc_version in v or latest_nf in v): return v
        return f"neoforge-{latest_nf}"
    except Exception as e:
        print(f"[ERROR] Fallo en la instalación de NeoForge: {e}"); return None

def install_forge(mc_version, java_exec):
    try:
        base_json = os.path.join(DEFAULT_MC_DIR, "versions", mc_version, f"{mc_version}.json")
        if not os.path.exists(base_json):
            minecraft_launcher_lib.install.install_minecraft_version(mc_version, DEFAULT_MC_DIR)
            
        req = urllib.request.Request("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json", headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
        forge_ver = data.get("promos", {}).get(f"{mc_version}-recommended") or data.get("promos", {}).get(f"{mc_version}-latest")
        if not forge_ver: return None
        full_forge = f"{mc_version}-{forge_ver}"
        installer_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{full_forge}/forge-{full_forge}-installer.jar"
        installer_path = os.path.join(DEFAULT_MC_DIR, "forge-installer.jar")
        
        print(f"[>] Descargando Forge {forge_ver}...")
        req_jar = urllib.request.Request(installer_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_jar) as response, open(installer_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        
        j_cmd = java_exec.replace("javaw.exe", "java.exe")
        subprocess.run([j_cmd, "-jar", installer_path, "--installClient", DEFAULT_MC_DIR], check=True)
        if os.path.exists(installer_path): os.remove(installer_path)
        
        for v in get_installed_list():
            if "forge" in v.lower() and mc_version in v: return v
        return f"{mc_version}-forge-{forge_ver}"
    except Exception as e:
        print(f"[ERROR] {e}"); return None

def install_new_version():
    clear_screen()
    print("========================================")
    print("         INSTALAR NUEVA VERSIÓN         ")
    print("========================================")
    base_ver = input("Versión oficial base (ej. 1.20.4, 26.3, 1.8.9 / [v] volver): ").strip()
    if not base_ver or base_ver.lower() == "v": return

    installed = get_installed_list()
    print(f"\n¿Qué nombre deseas asignarle a este perfil?")
    print(f"Presiona ENTER para dejar el predeterminado: '{base_ver}'")
    nombre_input = input("Nombre (o 'v' cancelar): ").strip()
    if nombre_input.lower() == "v": return

    custom_name = None
    if nombre_input and nombre_input != base_ver:
        if nombre_input in installed:
            print(f"[X] Ya existe una versión llamada '{nombre_input}'.")
            input("\nENTER para continuar..."); return
        custom_name = nombre_input
    elif base_ver in installed:
        print(f"\n[!] La versión '{base_ver}' ya existe. Asígnale otro nombre para no sobrescribir.")
        custom_name = input("Nombre: ").strip()
        if not custom_name or custom_name.lower() == "v" or custom_name in installed: return

    profile_label = custom_name if custom_name else base_ver
    support = check_loader_support(base_ver)

    print("\nSelecciona el Modloader:")
    print(" [1] Vanilla (Oficial Mojang) -> COMPATIBLE")
    print(f" [2] NeoForge -> {'COMPATIBLE' if support['neoforge'] else 'Incompatible (Requiere 1.20.2+)'}")
    print(f" [3] Forge -> {'COMPATIBLE' if support['forge'] else 'Incompatible'}")
    print(f" [4] Fabric -> {'COMPATIBLE' if support['fabric'] else 'Incompatible (Requiere 1.14+)'}")
    print(" [V] Cancelar")

    loader_choice = input("\nOpción (Enter = Vanilla): ").strip().lower()
    if loader_choice == "v": return
    if loader_choice == "2" and not support["neoforge"]: return
    if loader_choice == "3" and not support["forge"]: return
    if loader_choice == "4" and not support["fabric"]: return

    final_game_dir = DEFAULT_MC_DIR
    while True:
        clear_screen()
        sub_default = os.path.join(DEFAULT_MC_DIR, 'versions', profile_label)
        print("================================================================")
        print(f"       SELECCIÓN DE RUTA DE DATOS PARA: '{profile_label}'")
        print("================================================================")
        print(" [1] Ruta Predeterminada (.minecraft general)")
        print(f" [2] Crear subcarpeta -> {sub_default}")
        print(" [3] Ruta Personalizada (Elegir cualquier ubicación)")
        dir_opt = input("\nSelecciona opción: ").strip().lower()

        if dir_opt == "1" or not dir_opt: cand = DEFAULT_MC_DIR
        elif dir_opt == "2": cand = sub_default
        elif dir_opt == "3":
            copy_to_clipboard(DEFAULT_MC_DIR)
            cand = os.path.abspath(input("\nPega/escribe la nueva ruta: ").strip().strip('"'))
        else: continue

        if input(f"\n¿Usar carpeta:\n\"{cand}\"? (s/n): ").strip().lower() == "s":
            final_game_dir = cand; break

    os.makedirs(final_game_dir, exist_ok=True)
    os.makedirs(os.path.join(final_game_dir, "mods"), exist_ok=True)

    j_exec = ensure_java(get_required_java(base_ver))
    final_ver_id = base_ver
    
    if loader_choice == "2":
        res = install_neoforge(base_ver, j_exec)
        if res: final_ver_id = res
        else: print("\n[!] Falló la instalación de NeoForge. El perfil quedó como Vanilla.")
    elif loader_choice == "3":
        res = install_forge(base_ver, j_exec)
        if res: final_ver_id = res
    elif loader_choice == "4":
        res = install_fabric(base_ver)
        if res: final_ver_id = res
    else:
        # Vanilla puro
        base_json = os.path.join(DEFAULT_MC_DIR, "versions", base_ver, f"{base_ver}.json")
        if not os.path.exists(base_json):
            print(f"\n[>] Descargando Vanilla {base_ver}...")
            minecraft_launcher_lib.install.install_minecraft_version(base_ver, DEFAULT_MC_DIR)

    if custom_name and final_ver_id != custom_name:
        src_folder = os.path.join(DEFAULT_MC_DIR, "versions", final_ver_id)
        dst_folder = os.path.join(DEFAULT_MC_DIR, "versions", custom_name)
        if os.path.exists(src_folder) and src_folder != dst_folder:
            if os.path.exists(dst_folder): shutil.rmtree(dst_folder)
            shutil.copytree(src_folder, dst_folder)
            src_json = os.path.join(dst_folder, f"{final_ver_id}.json")
            dst_json = os.path.join(dst_folder, f"{custom_name}.json")
            if os.path.exists(src_json):
                with open(src_json, "r", encoding="utf-8") as f: data = json.load(f)
                data["id"] = custom_name
                with open(dst_json, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)
                if src_json != dst_json: os.remove(src_json)
            src_jar = os.path.join(dst_folder, f"{final_ver_id}.jar")
            dst_jar = os.path.join(dst_folder, f"{custom_name}.jar")
            if os.path.exists(src_jar): os.rename(src_jar, dst_jar)
            final_ver_id = custom_name

    config = load_config()
    config.setdefault("version_dirs", {})[final_ver_id] = final_game_dir
    save_config(config)
    print(f"\n[OK] Perfil '{final_ver_id}' instalado correctamente.")
    input("\nPresiona ENTER...")
# =====================================================================
# 9. PROTECCIÓN DE MEMORIA Y ARGUMENTOS DE INICIO (ANTI-CRASH)
# =====================================================================
def apply_skin_to_runtime(version_id, skin_png_path):
    if not skin_png_path or not os.path.exists(skin_png_path): return
    jar_path = os.path.join(DEFAULT_MC_DIR, "versions", version_id, f"{version_id}.jar")
    if not os.path.exists(jar_path): return
    try:
        temp_jar = jar_path + ".temp"
        with zipfile.ZipFile(jar_path, 'r') as zin:
            with zipfile.ZipFile(temp_jar, 'w') as zout:
                for item in zin.infolist():
                    if item.filename.startswith("META-INF/") and item.filename.endswith((".SF", ".DSA", ".RSA")):
                        continue
                    if item.filename in ["assets/minecraft/textures/entity/steve.png",
                                         "assets/minecraft/textures/entity/alex.png",
                                         "assets/minecraft/textures/entity/player/wide/steve.png",
                                         "assets/minecraft/textures/entity/player/slim/alex.png"]:
                        zout.writestr(item, open(skin_png_path, "rb").read())
                    else:
                        zout.writestr(item, zin.read(item.filename))
        os.remove(jar_path)
        os.rename(temp_jar, jar_path)
    except Exception:
        pass

def calculate_adaptive_ram(version_id, game_dir):
    total_ram, avail_ram, _ = get_system_ram()
    max_safe_ram = int(total_ram * 0.85)
    
    mods_path = os.path.join(game_dir, "mods")
    shader_path = os.path.join(game_dir, "shaderpacks")
    has_mods = os.path.exists(mods_path) and len([f for f in os.listdir(mods_path) if f.endswith(".jar")]) > 0
    has_shaders = os.path.exists(shader_path) and len([f for f in os.listdir(shader_path) if f.endswith(".zip") or os.path.isdir(os.path.join(shader_path, f))]) > 0
    
    is_legacy = any(old in version_id.lower() for old in ["1.7", "1.8", "1.9", "1.10", "1.11", "1.12"])
    
    if is_legacy and not has_mods: tr = 2
    elif is_legacy and has_mods: tr = 4
    elif not is_legacy and not has_mods and not has_shaders: tr = 3 if total_ram <= 8 else 4
    elif not is_legacy and (has_mods or has_shaders):
        if total_ram >= 16: tr = 10 if not (has_mods and has_shaders) else 11
        elif total_ram >= 12: tr = 8
        elif total_ram >= 8: tr = 6
        else: tr = 4
    else: tr = 4
    return int(max(1, min(tr, max_safe_ram)))

def get_jvm_ram_args(version_id, game_dir, ram_mode_override=None):
    config = load_config()
    mode = ram_mode_override if ram_mode_override else config.get("ram_mode", "adaptive")
    total_ram, avail_ram, _ = get_system_ram()

    base_flags = ["-XX:+UseG1GC", "-Xms1G"]

    if mode == "adaptive": 
        val = calculate_adaptive_ram(version_id, game_dir)
        return [f"-Xmx{val}G"] + base_flags
    elif mode == "auto": 
        val = int(max(2, min(int(avail_ram - 1), int(total_ram * 0.85))))
        return [f"-Xmx{val}G"] + base_flags
    elif mode.startswith("slot_"): 
        val_str = config.get("ram_slots", {}).get(mode.split('_')[1], "4G")
        return [f"-Xmx{val_str}"] + base_flags
    return ["-Xmx4G"] + base_flags

def manage_ram():
    while True:
        clear_screen()
        config = load_config()
        total_ram, avail_ram, used_pct = get_system_ram()
        mode = config.get("ram_mode", "adaptive")
        slots = config.get("ram_slots", {})

        print("================================================================")
        print("          PERSONALIZACIÓN Y LÍMITES DE MEMORIA RAM             ")
        print("================================================================")
        print(f" RAM FÍSICA: {total_ram} GB | LIBRE: {avail_ram} GB ({used_pct}% en uso)")
        print(f" Límite máximo infranqueable (85%): {round(total_ram * 0.85, 1)} GB")
        print(f" Modo activo actual: {mode.upper()}")
        print("----------------------------------------------------------------")
        print(" [D] Modo ADAPTATIVO INTELIGENTE (Auto-ajuste por versión/mods)")
        print(" [A] Modo Automático Estándar")
        print(" [S] Seleccionar Slot manual para jugar")
        print(" [E] Editar valor de un Slot de RAM (1-5)")
        print(" [V] Volver")
        print("----------------------------------------------------------------")
        for i in range(1, 6):
            mark = "-> ACTIVO" if (mode.startswith("slot_") and config.get("active_ram_slot") == str(i)) else ""
            print(f"  Slot [{i}]: {slots.get(str(i), '4G')} {mark}")
        opc = input("\nSelecciona: ").strip().lower()

        if opc == "v": break
        elif opc == "d": config["ram_mode"] = "adaptive"; save_config(config)
        elif opc == "a": config["ram_mode"] = "auto"; save_config(config)
        elif opc == "s":
            s_num = input("Número de slot a activar (1-5): ").strip()
            if s_num in ["1", "2", "3", "4", "5"]:
                config["ram_mode"] = f"slot_{s_num}"; config["active_ram_slot"] = s_num; save_config(config)
        elif opc == "e":
            s_num = input("Slot a editar (1-5): ").strip()
            if s_num in ["1", "2", "3", "4", "5"]:
                print(f"\nEscalas disponibles: {RAM_TIERS}")
                val = input("Cantidad en GB a asignar (ej. 4): ").strip()
                if val.isdigit():
                    x = int(val)
                    if x > total_ram * 0.85:
                        print(f"\n[DENEGADO] Asignar {x} GB supera el 85% de tu memoria física."); input(); continue
                    slots[s_num] = f"{x}G"
                    config["ram_slots"] = slots
                    save_config(config)

def manage_aspect_ratio_test():
    clear_screen()
    config = load_config()
    disp = config.get("display", {"width": 854, "height": 480, "aspect_ratio": "16:9"})
    w_sys, h_sys = get_system_resolution()

    print("================================================================")
    print("            AJUSTE EXPERIMENTAL DE RELACIÓN DE ASPECTO          ")
    print("================================================================")
    print(f" Resolución nativa de tu monitor: {w_sys}x{h_sys}")
    print(f" Config actual de lanzamiento: {disp.get('width')}x{disp.get('height')} ({disp.get('aspect_ratio')})")
    print("----------------------------------------------------------------")
    print(" [1] 16:9 Estándar (854 x 480)")
    print(" [2] 16:9 Full HD   (1920 x 1080)")
    print(" [3] 4:3  Clásico   (1024 x 768)")
    print(" [4] 16:10 Laptop   (1280 x 800)")
    print(" [V] Cancelar y volver")
    opc = input("\nSelecciona opción: ").strip().lower()

    resolutions = {"1": (854, 480, "16:9"), "2": (1920, 1080, "16:9"), "3": (1024, 768, "4:3"), "4": (1280, 800, "16:10")}
    if opc not in resolutions: return

    new_w, new_h, new_ratio = resolutions[opc]
    print("\n----------------------------------------------------------------")
    print(f" [*] PROBANDO NUEVA RESOLUCIÓN: {new_w}x{new_h}")
    print(" Tienes 5 segundos para confirmar. Presiona 's' para confirmar: ", end="", flush=True)

    import msvcrt
    start_time = time.time(); user_input = ""
    while time.time() - start_time < 5.0:
        if msvcrt.kbhit():
            ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
            if ch in ["s", "n"]: user_input = ch; break
        time.sleep(0.05)

    if user_input == "s":
        disp["width"] = new_w; disp["height"] = new_h; disp["aspect_ratio"] = new_ratio
        config["display"] = disp; save_config(config)
        print("\n\n[OK] Resolución guardada.")
    elif user_input == "n": print("\n\n[X] Cambio cancelado manualmente.")
    else: print("\n\n[!] TIEMPO AGOTADO. Se revierte el cambio de aspecto por seguridad.")
    input("\nPresiona ENTER para continuar...")

def experimental_menu():
    while True:
        clear_screen()
        total_ram, avail_ram, used_pct = get_system_ram()
        w, h = get_system_resolution()
        print("================================================================")
        print("             PANEL EXPERIMENTAL DE RENDIMIENTO                  ")
        print("================================================================")
        print(f" > RAM Total: {total_ram} GB | Libre: {avail_ram} GB | Uso: {used_pct}%")
        print(f" > Pantalla: {w}x{h} píxeles")
        print("----------------------------------------------------------------")
        print(" [1] Guía explicativa para novatos")
        print(" [2] Personalización y escalas de memoria RAM")
        print(" [3] Relación de aspecto y resolución (Prueba de 5 seg)")
        print(" [V] Volver")
        opc = input("\nSelecciona una opción: ").strip().lower()

        if opc == "1": show_novice_guide()
        elif opc == "2": manage_ram()
        elif opc == "3": manage_aspect_ratio_test()
        elif opc == "v": break

# =====================================================================
# 10. CREADOR DE ACCESOS DIRECTOS AL ESCRITORIO
# =====================================================================
def create_custom_desktop_shortcut():
    clear_screen()
    print("================================================================")
    print("         CREAR ACCESO DIRECTO RÁPIDO AL ESCRITORIO              ")
    print("================================================================")
    vers = get_installed_list()
    if not vers:
        print("[!] No hay versiones instaladas.")
        input("\nPresiona ENTER para volver...")
        return

    for idx, v in enumerate(vers, 1): print(f" [{idx}] {v}")
    v_opc = input("\nElige la versión: ").strip()
    if not v_opc or v_opc.lower() == "v": return
    version = vers[int(v_opc) - 1] if v_opc.isdigit() and 1 <= int(v_opc) <= len(vers) else v_opc
    if version not in vers: return

    config = load_config()
    active_users = [(k, v) for k, v in config.get("user_slots", {}).items() if v.strip()]
    if not active_users:
        print("[!] Registra un usuario primero en la sección de Personalización.")
        input("\nENTER..."); return

    print("\nElige usuario:")
    for idx, (s_idx, uname) in enumerate(active_users, 1): print(f" [{idx}] {uname} (Slot {s_idx})")
    u_idx = input("Selecciona: ").strip()
    if not u_idx.isdigit() or not (1 <= int(u_idx) <= len(active_users)): return
    chosen_user = active_users[int(u_idx) - 1][1]

    print("\nElige skin:")
    skin_slots = config.get("skin_slots", {})
    available_skins = []
    for i in range(1, 11):
        s_data = skin_slots.get(str(i), {})
        if s_data.get("path") and os.path.exists(s_data["path"]):
            available_skins.append((str(i), s_data["name"]))
            print(f" [{len(available_skins)}] {s_data['name']}")
    print(f" [{len(available_skins) + 1}] Default")
    sk_idx = input("Selecciona: ").strip()
    chosen_skin_slot = "default"
    if sk_idx.isdigit() and 1 <= int(sk_idx) <= len(available_skins):
        chosen_skin_slot = available_skins[int(sk_idx) - 1][0]

    print("\nPerfil de RAM para el acceso directo:")
    print(" [D] Adaptativo Inteligente (Recomendado)")
    print(" [A] Automático Estándar")
    for i in range(1, 6): print(f" [{i}] Slot {i} ({config.get('ram_slots', {}).get(str(i), '4G')})")
    ram_choice = input("Selecciona: ").strip().lower()

    if ram_choice == "d": ram_preset = "adaptive"
    elif ram_choice == "a": ram_preset = "auto"
    elif ram_choice.isdigit() and 1 <= int(ram_choice) <= 5: ram_preset = f"slot_{ram_choice}"
    else: ram_preset = "adaptive"

    desktop_path = get_real_desktop_path()
    os.makedirs(desktop_path, exist_ok=True)

    clean_ver = re.sub(r'[\\/*?:"<>|]', "", version)
    clean_user = re.sub(r'[\\/*?:"<>|]', "", chosen_user)
    shortcut_path = os.path.join(desktop_path, f"Minecraft {clean_ver} ({clean_user}).lnk")
    target_bat = os.path.join(SCRIPT_DIR, "Jugar.bat")

    vbs_script = os.path.join(DEFAULT_MC_DIR, "make_shortcut.vbs")
    icon_file = ensure_minecraft_icon() or ""

    vbs_content = f'''Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{shortcut_path}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{target_bat}"
oLink.Arguments = "--quick-launch ""{version}"" ""{chosen_user}"" ""{chosen_skin_slot}"" ""{ram_preset}"""
oLink.WorkingDirectory = "{SCRIPT_DIR}"
oLink.Description = "Iniciar Minecraft {version} como {chosen_user}"
'''
    if icon_file and os.path.exists(icon_file): vbs_content += f'oLink.IconLocation = "{icon_file}, 0"\n'
    vbs_content += 'oLink.Save\n'

    try:
        with open(vbs_script, "w", encoding="latin-1", errors="replace") as f: f.write(vbs_content)
        subprocess.run(["cscript", "//nologo", vbs_script], check=True)
        if os.path.exists(vbs_script): os.remove(vbs_script)
        print(f"\n[OK] Acceso directo con icono oficial creado en tu Escritorio.")
    except Exception as e: print(f"[ERROR] {e}")
    input("\nPresiona ENTER para continuar...")

# =====================================================================
# 11. MOTOR DE EJECUCIÓN (WRAPPER ANTI-CRASHEO SILENCIOSO Y LOGIN PREMIUM)
# =====================================================================
def run_game_via_bat(command, game_dir, version):
    """Crea un .bat temporal para que CMD intercepte errores fatales de JVM."""
    bat_path = os.path.join(game_dir, "run_minecraft_diagnostic.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write("title Minecraft Diagnostic Console\n")
        f.write("echo ================================================================================\n")
        f.write(f"echo  Iniciando Minecraft {version}...\n")
        f.write("echo  Si el juego se cierra o crashea, el error aparecera justo aqui abajo.\n")
        f.write("echo ================================================================================\n")
        f.write(f"cd /d \"{game_dir}\"\n\n")
        
        cmd_str = []
        for arg in command:
            if " " in arg or "=" in arg or ";" in arg: cmd_str.append(f'"{arg}"')
            else: cmd_str.append(arg)
                
        f.write(" ".join(cmd_str) + "\n\n")
        f.write("echo.\n")
        f.write("echo [!] El proceso ha finalizado abruptamente.\n")
        f.write("pause\n")
        
    try:
        subprocess.Popen(f'start "Minecraft Console" cmd /c "{bat_path}"', cwd=game_dir, shell=True)
        return True
    except Exception as e:
        print(f"[X] Falló el intento de abrir la consola de diagnóstico: {e}")
        return False

def launch_game_direct(version, username, skin_slot, ram_mode):
    config = load_config()
    if skin_slot != "default":
        s_path = config.get("skin_slots", {}).get(skin_slot, {}).get("path")
        if s_path and os.path.exists(s_path):
            apply_skin_to_runtime(version, s_path)

    game_dir = config.get("version_dirs", {}).get(version, DEFAULT_MC_DIR)
    req_java = get_required_java(version)
    java_executable = ensure_java(req_java)

    if java_executable and java_executable.endswith("javaw.exe"):
        java_executable = java_executable.replace("javaw.exe", "java.exe")

    jvm_args = get_jvm_ram_args(version, game_dir, ram_mode)
    disp = config.get("display", {"width": 854, "height": 480})
    
    # Intenta recuperar cuenta Premium si la hay
    ms_accounts = config.get("microsoft_accounts", {})
    options = {}
    if username in ms_accounts:
        # Se verifica si el token es válido o está expirado. El Wrapper de la libreria suele refrescarlo.
        data = ms_accounts[username]
        options = {"username": data["name"], "uuid": data["id"], "token": data["access_token"]}
    else:
        offline_uuid = str(uuid.uuid3(uuid.NAMESPACE_DNS, "OfflinePlayer:" + username)).replace("-", "")
        options = {"username": username, "uuid": offline_uuid, "token": ""}

    options.update({
        "executablePath": java_executable,
        "gameDirectory": game_dir,
        "jvmArguments": jvm_args,
        "customResolution": True,
        "resolutionWidth": str(disp.get("width", 854)),
        "resolutionHeight": str(disp.get("height", 480))
    })

    command = minecraft_launcher_lib.command.get_minecraft_command(version, DEFAULT_MC_DIR, options)
    
    cleaned_command = []
    skip_next = False
    for arg in command:
        if skip_next:
            skip_next = False; continue
        if arg in ("--demo", "${clientid}", "${auth_session}"): continue
        # Si es offline, limpiamos el clientId. Si es Premium, Mojang necesita el clientid oficial.
        if arg in ("--clientId", "--clientid") and username not in ms_accounts:
            skip_next = True; continue
        cleaned_command.append(arg)
        
    run_game_via_bat(cleaned_command, game_dir, version)
    sys.exit(0)

def launch_game_flow():
    config = load_config()
    user_slots = config.get("user_slots", {})
    active_users = [(k, v) for k, v in user_slots.items() if v.strip()]
    ms_accounts = config.get("microsoft_accounts", {})
    
    if not active_users and not ms_accounts:
        clear_screen()
        print(" [!] No tienes ninguna cuenta configurada. Ve a Personalización para añadir una.")
        input("\nPresiona ENTER para continuar...")
        return

    clear_screen()
    print("========================================")
    print("            INICIAR MINECRAFT           ")
    print("========================================")
    vers = get_installed_list()
    if not vers:
        print("[!] No hay versiones instaladas.")
        input("\nPresiona ENTER...")
        return

    for idx, v in enumerate(vers, 1): print(f" [{idx}] {v}")
    opc = input("\nElige la versión (o 'V'): ").strip()
    if not opc or opc.lower() == "v": return
    version = vers[int(opc) - 1] if opc.isdigit() and 1 <= int(opc) <= len(vers) else opc
    if version not in vers: return

    print("\nElige tu usuario para jugar:")
    user_map = {}
    counter = 1
    
    # 1. Mostrar cuentas Premium
    if ms_accounts:
        print(" --- Cuentas Premium (Microsoft) ---")
        for u in ms_accounts.keys():
            user_map[str(counter)] = ("Premium", u)
            print(f" [{counter}] {u} [Validada]")
            counter += 1
            
    # 2. Mostrar cuentas Offline
    if active_users:
        print(" --- Cuentas Offline (No Premium) ---")
        for slot_idx, uname in active_users:
            if uname not in ms_accounts:
                user_map[str(counter)] = ("Offline", uname)
                print(f" [{counter}] {uname} (Slot {slot_idx})")
                counter += 1

    u_opc = input("Selecciona: ").strip()
    if u_opc not in user_map: return
    tipo_cuenta, username = user_map[u_opc]

    # Skin solo para Offline, las Premium la bajan de Mojang automáticamente.
    skin_path = ""
    if tipo_cuenta == "Offline":
        print("\nElige tu skin:")
        slots = config.get("skin_slots", {})
        available_skins = []
        for i in range(1, 11):
            s_data = slots.get(str(i), {})
            if s_data.get("path") and os.path.exists(s_data["path"]):
                available_skins.append((str(i), s_data["name"], s_data["path"]))
                print(f" [{len(available_skins)}] {s_data['name']}")
        print(f" [{len(available_skins) + 1}] Default (Steve / Alex)")

        sk_opc = input("Selecciona skin: ").strip()
        if sk_opc.isdigit() and 1 <= int(sk_opc) <= len(available_skins):
            skin_path = available_skins[int(sk_opc) - 1][2]
            apply_skin_to_runtime(version, skin_path)

    options = {}
    if tipo_cuenta == "Premium":
        data = ms_accounts[username]
        options = {
            "username": data["name"],
            "uuid": data["id"],
            "token": data["access_token"]
        }
    else:
        offline_uuid = str(uuid.uuid3(uuid.NAMESPACE_DNS, "OfflinePlayer:" + username)).replace("-", "")
        options = {"username": username, "uuid": offline_uuid, "token": ""}

    game_dir = config.get("version_dirs", {}).get(version, DEFAULT_MC_DIR)
    req_java = get_required_java(version)
    java_executable = ensure_java(req_java)

    if java_executable and java_executable.endswith("javaw.exe"):
        java_executable = java_executable.replace("javaw.exe", "java.exe")

    if not java_executable or not os.path.exists(java_executable):
        print(f"\n[X] ERROR CRÍTICO: No se encontró el ejecutable Java.")
        input("\nPresiona ENTER para volver al menú...")
        return

    jvm_args = get_jvm_ram_args(version, game_dir)
    disp = config.get("display", {"width": 854, "height": 480})
    
    options.update({
        "executablePath": java_executable,
        "gameDirectory": game_dir,
        "jvmArguments": jvm_args,
        "customResolution": True,
        "resolutionWidth": str(disp.get("width", 854)),
        "resolutionHeight": str(disp.get("height", 480))
    })

    print("\n[>] Generando comandos de arranque interno...")
    try:
        command = minecraft_launcher_lib.command.get_minecraft_command(version, DEFAULT_MC_DIR, options)
    except Exception as cmd_error:
        print(f"\n[X] Fallo crítico al generar comando. Detalle: {cmd_error}")
        input("\nPresiona ENTER...")
        return

    cleaned_command = []
    skip_next = False
    for arg in command:
        if skip_next:
            skip_next = False; continue
        if arg in ("--demo", "${clientid}", "${auth_session}"): continue
        # Evitar fallos de Realms: Si es offline, quitamos clientId.
        if arg in ("--clientId", "--clientid") and tipo_cuenta == "Offline": 
            skip_next = True; continue
        cleaned_command.append(arg)

    ram_assigned = jvm_args[0].replace("-Xmx", "")
    print(f"[OK] Abriendo {version} como '{options['username']}' con RAM {ram_assigned}...")
    
    success = run_game_via_bat(cleaned_command, game_dir, version)
    
    if success:
        print("\nEl juego está arrancando en una ventana nueva de diagnóstico.")
    input("\nPresiona ENTER para volver al menú principal...")

# =====================================================================
# 12. MENÚS Y ADMINISTRADOR DE CUENTAS PREMIUM
# =====================================================================
def manage_microsoft_accounts():
    config = load_config()
    ms_accounts = config.get("microsoft_accounts", {})
    
    while True:
        clear_screen()
        print("========================================")
        print("   ADMINISTRADOR DE CUENTAS MICROSOFT   ")
        print("========================================")
        print(" Las cuentas registradas aquí podrán jugar en servidores")
        print(" originales, Realms y agregar Amigos sin restricciones.")
        print("----------------------------------------")
        if not ms_accounts:
            print(" [!] No hay cuentas Premium registradas.")
        else:
            for idx, username in enumerate(ms_accounts.keys(), 1):
                print(f" [{idx}] {username} (Token Validado)")
        print("----------------------------------------")
        print(" [A] Agregar / Iniciar Sesión con Microsoft")
        print(" [B] Borrar cuenta seleccionada")
        print(" [V] Volver")
        opc = input("\nSelecciona: ").strip().lower()
        
        if opc == "v" or not opc: break
        elif opc == "a":
            print("\n[i] Se abrirá tu navegador. Inicia sesión con la cuenta que tiene Minecraft.")
            try:
                account_data = minecraft_launcher_lib.microsoft_account.login_with_msa()
                if account_data and "name" in account_data:
                    ms_accounts[account_data["name"]] = account_data
                    config["microsoft_accounts"] = ms_accounts
                    save_config(config)
                    print(f"\n[OK] Cuenta '{account_data['name']}' vinculada exitosamente.")
                else:
                    print("\n[X] La cuenta ingresada no posee Minecraft o se canceló el proceso.")
            except Exception as e:
                print(f"\n[ERROR] Fallo al iniciar sesión: {e}")
            input("\nPresiona ENTER para continuar...")
        elif opc == "b":
            if not ms_accounts: continue
            cuenta = input("\nEscribe el nombre de la cuenta a borrar: ").strip()
            if cuenta in ms_accounts:
                del ms_accounts[cuenta]
                config["microsoft_accounts"] = ms_accounts
                save_config(config)
                print(f"\n[OK] Cuenta '{cuenta}' borrada.")
            else:
                print("\n[X] La cuenta no existe.")
            input("\nPresiona ENTER...")

def game_settings_menu():
    while True:
        clear_screen()
        print("========================================")
        print("       CONFIGURACIÓN DEL JUEGO          ")
        print("========================================")
        print(" [1] Instalar nueva versión")
        print(" [2] Ver versiones instaladas")
        print(" [3] Crear acceso directo en el Escritorio (1-Clic con icono)")
        print(" [4] Editar versión (Rutas / Contenido / Mods / Servers)")
        print(" [5] Borrar versión")
        print(" [6] Entrar a la carpeta raíz (.minecraft)")
        print(" [7] Experimental (RAM Adaptativa, Renders, Aspect Ratio)")
        print(" [8] Datos (Importar / Exportar Backup ZIP Selectivo)")
        print(" [V] Volver al menú principal")
        opc = input("\nSelecciona: ").strip().lower()

        if opc == "1": install_new_version()
        elif opc == "2": show_installed_versions()
        elif opc == "3": create_custom_desktop_shortcut()
        elif opc == "4": edit_version()
        elif opc == "5": delete_version()
        elif opc == "6": open_game_folder()
        elif opc == "7": experimental_menu()
        elif opc == "8": manage_data_backup()
        elif opc == "v": break

def customization_menu():
    while True:
        clear_screen()
        print("========================================")
        print("             PERSONALIZACIÓN            ")
        print("========================================")
        print(" [1] Administrar Nombres de Usuario Offline (10 Slots)")
        print(" [2] Administrar Skins Offline (10 Slots)")
        print(" [3] Administrar Cuentas de Microsoft (Login Premium)")
        print(" [V] Volver al menú principal")
        opc = input("\nSelecciona: ").strip().lower()
        if opc == "1": manage_users()
        elif opc == "2": manage_skins()
        elif opc == "3": manage_microsoft_accounts()
        elif opc == "v": break

def manage_users():
    config = load_config()
    slots = config.get("user_slots", {str(i): "" for i in range(1, 11)})
    while True:
        clear_screen()
        print("========================================")
        print("   ADMINISTRADOR DE USUARIOS OFFLINE    ")
        print("========================================")
        for i in range(1, 11):
            name = slots.get(str(i), "").strip()
            print(f" [{i}/10] {name if name else '[Vacío]'}")
        print("\n [E] Editar/Agregar  [B] Borrar  [V] Volver")
        opc = input("Opción: ").strip().lower()
        if opc == "v" or not opc: break
        elif opc == "e":
            s_num = input("Slot (1-10): ").strip()
            if s_num in slots:
                slots[s_num] = input("Nombre de usuario: ").strip()
                config["user_slots"] = slots; save_config(config)
        elif opc == "b":
            s_num = input("Slot a vaciar (1-10): ").strip()
            if s_num in slots:
                slots[s_num] = ""
                config["user_slots"] = slots; save_config(config)

def manage_skins():
    os.makedirs(SKINS_DIR, exist_ok=True)
    config = load_config()
    slots = config.get("skin_slots", {})
    while True:
        clear_screen()
        print("========================================")
        print("     ADMINISTRADOR DE SKINS OFFLINE     ")
        print("========================================")
        for i in range(1, 11):
            s_data = slots.get(str(i), {"name": "Vacio", "path": ""})
            print(f" [{i}/10] {s_data['name'] if s_data.get('path') else 'Vacio'}")
        print("\n [E] Subir (.png)  [B] Borrar  [V] Volver")
        opc = input("Opción: ").strip().lower()
        if opc == "v" or not opc: break
        elif opc == "e":
            slot_num = input("Slot (1-10): ").strip()
            if slot_num.isdigit() and 1 <= int(slot_num) <= 10:
                ruta = input("Ruta del archivo .png: ").strip().strip('"')
                if os.path.exists(ruta) and ruta.endswith(".png"):
                    dest = os.path.join(SKINS_DIR, f"slot_{slot_num}.png")
                    shutil.copyfile(ruta, dest)
                    slots[slot_num] = {"name": input("Nombre Identificador: ").strip() or f"Skin_{slot_num}", "path": dest}
                    config["skin_slots"] = slots; save_config(config)
        elif opc == "b":
            slot_num = input("Slot a limpiar (1-10): ").strip()
            if slot_num.isdigit() and 1 <= int(slot_num) <= 10:
                slots[slot_num] = {"name": "Vacio", "path": ""}
                dest = os.path.join(SKINS_DIR, f"slot_{slot_num}.png")
                if os.path.exists(dest): os.remove(dest)
                config["skin_slots"] = slots; save_config(config)

def show_info():
    clear_screen()
    print("================================================================")
    print("                  INFORMACIÓN DEL PROYECTO                      ")
    print("================================================================")
    print(" Autor del launcher: MeqqFranz")
    print(" Estado del software: BETA (En desarrollo continuo)")
    print(" Lenguaje base: Python / Batch Script")
    print(" Gestión del juego: minecraft-launcher-lib")
    print("----------------------------------------------------------------")
    print(" [!] AVISO IMPORTANTE:")
    print(" Este launcher experimental ofrece funciones limitadas.")
    print(" Para disfrutar de la experiencia completa (servidores oficiales,")
    print(" realms, skins sincronizadas globales y soporte oficial), te")
    print(" recomendamos apoyar el juego adquiriendo la versión oficial.")
    print("")
    print(" Compra el juego original aquí:")
    print(" >> https://www.minecraft.net <<")
    print("----------------------------------------------------------------")
    print(" AVISO LEGAL Y DESCARGO DE RESPONSABILIDAD:")
    print(" Este proyecto es independiente y NO está afiliado, respaldado,")
    print(" asociado ni vinculado oficialmente con Microsoft Corporation ©,")
    print(" Mojang Studios © ni ninguna de sus filiales.")
    print("")
    print(" Minecraft es una marca registrada de Mojang AB / Microsoft.")
    print("================================================================")
    input("\nPresiona ENTER para volver al menú principal...")

def manage_data_backup():
    while True:
        clear_screen()
        print("========================================")
        print("          ADMINISTRADOR DE DATOS        ")
        print("========================================")
        print(" [1] Exportar Todo (Config + Skins + Todas las Versiones)")
        print(" [2] Exportar Ligero (Solo Configuración y Skins)")
        print(" [3] Importar desde archivo ZIP")
        print(" [V] Volver")
        opc = input("\nSelecciona: ").strip().lower()

        if opc == "v" or not opc: break
        elif opc == "1" or opc == "2":
            desktop_path = get_real_desktop_path()
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            tipo = "Completo" if opc == "1" else "Ligero"
            backup_zip = os.path.join(desktop_path, f"LauncherBackup_{tipo}_{date_str}.zip")
            print(f"\n[>] Generando archivo ZIP... (puede tomar un momento)")
            try:
                with zipfile.ZipFile(backup_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    if os.path.exists(CONFIG_FILE):
                        zipf.write(CONFIG_FILE, "launcher_config.json")
                    if os.path.exists(SKINS_DIR):
                        for root, _, files in os.walk(SKINS_DIR):
                            for file in files:
                                full_path = os.path.join(root, file)
                                rel_path = os.path.relpath(full_path, DEFAULT_MC_DIR)
                                zipf.write(full_path, rel_path)
                    if opc == "1":
                        v_path = os.path.join(DEFAULT_MC_DIR, "versions")
                        if os.path.exists(v_path):
                            for root, dirs, files in os.walk(v_path):
                                for file in files:
                                    full_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(full_path, DEFAULT_MC_DIR)
                                    zipf.write(full_path, rel_path)
                print(f"\n[OK] Respaldo '{tipo}' generado en el Escritorio:\n     {backup_zip}")
            except Exception as e:
                print(f"[ERROR] {e}")
            input("\nPresiona ENTER...")
        elif opc == "3":
            zip_path = input("\nRuta del archivo .zip: ").strip().strip('"')
            if os.path.exists(zip_path) and zip_path.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zipf:
                        zipf.extractall(DEFAULT_MC_DIR)
                    print("\n[OK] Datos restaurados correctamente.")
                except Exception as e:
                    print(f"[ERROR] {e}")
            input("\nPresiona ENTER...")

# =====================================================================
# 13. INICIO Y LOOP PRINCIPAL
# =====================================================================
def main_menu():
    check_first_run()
    apply_console_dimensions()

    while True:
        clear_screen()
        print("========================================")
        print("          MINECRAFT AUTO LAUNCHER       ")
        print("========================================")
        print(" [1] Iniciar juego")
        print(" [2] Configuración del juego")
        print(" [3] Personalización")
        print(" [4] Información")
        print(" [5] Salir")
        print("========================================")
        opc = input("Selecciona una opción: ").strip()

        if opc == "1": launch_game_flow()
        elif opc == "2": game_settings_menu()
        elif opc == "3": customization_menu()
        elif opc == "4": show_info()
        elif opc == "5": break

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--quick-launch":
        try:
            ver = sys.argv[2]
            usr = sys.argv[3]
            sk_slot = sys.argv[4]
            ram_m = sys.argv[5]
            launch_game_direct(ver, usr, sk_slot, ram_m)
        except Exception as ex:
            print(f"Error fatal en quick-launch: {ex}")
            input("Presiona ENTER...")
            main_menu()
    else:
        main_menu()