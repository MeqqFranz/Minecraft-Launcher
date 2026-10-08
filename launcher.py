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
# 2. HARDWARE Y UTILIDADES DEL SISTEMA
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
    os.system("mode con: cols=100 lines=35")

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
    except Exception: pass
    return os.path.join(os.path.expanduser("~"), "Desktop")

# =====================================================================
# 3. MANEJO DE VERSIONES Y COMPATIBILIDAD
# =====================================================================
def extract_base_version(version_id):
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
# 4. MOTOR DE JAVA JVM Y DESCARGAS
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
# 5. GESTOR DE SERVIDORES NBT
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
# 6. CONFIGURACIÓN DEL LAUNCHER Y ESTADO INICIAL
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
        "microsoft_accounts": {}
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
# 8. GESTIÓN Y EDICIÓN DE VERSIONES (CARPETAS)
# =====================================================================
def open_game_folder():
    os.makedirs(DEFAULT_MC_DIR, exist_ok=True)
    os.makedirs(os.path.join(DEFAULT_MC_DIR, "mods"), exist_ok=True)
    os.makedirs(os.path.join(DEFAULT_MC_DIR, "shaderpacks"), exist_ok=True)
    os.makedirs(os.path.join(DEFAULT_MC_DIR, "resourcepacks"), exist_ok=True)
    try:
        os.startfile(DEFAULT_MC_DIR)
    except AttributeError:
        if sys.platform == "darwin": subprocess.call(["open", DEFAULT_MC_DIR])
        else: subprocess.call(["xdg-open", DEFAULT_MC_DIR])

def delete_version():
    clear_screen()
    print("========================================")
    print("            BORRAR VERSIÓN              ")
    print("========================================")
    vers = get_installed_list()
    for idx, v in enumerate(vers, 1): print(f" [{idx}] {v}")
    opc = input("\nSelecciona número a borrar (o 'V' para volver): ").strip()
    if opc.isdigit() and 1 <= int(opc) <= len(vers):
        target = vers[int(opc) - 1]
        if input(f"¿Estás 100% seguro de eliminar '{target}'? (s/n): ").strip().lower() == "s":
            folder = os.path.join(DEFAULT_MC_DIR, "versions", target)
            if os.path.exists(folder): shutil.rmtree(folder)
            config = load_config()
            config.get("version_dirs", {}).pop(target, None)
            save_config(config)
            print(f"[OK] Instancia eliminada por completo.")
            input("\nPresiona ENTER...")

def edit_version():
    clear_screen()
    print("========================================")
    print("             EDITAR VERSIÓN             ")
    print("========================================")
    vers = get_installed_list()
    for idx, v in enumerate(vers, 1): print(f" [{idx}] {v} [{detect_version_loader(v).upper()}]")
    opc = input("\nSelecciona versión a editar (o 'V' para volver): ").strip()
    if not opc or opc.lower() == "v": return

    if opc.isdigit() and 1 <= int(opc) <= len(vers):
        target = vers[int(opc) - 1]
        target_dir = os.path.join(DEFAULT_MC_DIR, "versions", target)
        json_file = os.path.join(target_dir, f"{target}.json")
        config = load_config()
        current_game_dir = config.get("version_dirs", {}).get(target, DEFAULT_MC_DIR)

        while True:
            clear_screen()
            current_loader = detect_version_loader(target).upper()
            print("================================================================")
            print(f" EDITANDO VERSIÓN: {target} [{current_loader}]")
            print(f" Directorio actual: {current_game_dir}")
            print("================================================================")
            print(" [1] Cambiar carpeta de datos (con confirmación S/N)")
            print(" [2] Renombrar nombre de la versión")
            print(" [3] Administrar contenido (Mods, Shaders, Mapas, Servers)")
            print(" [4] Inyectar Modloader (Si actualmente es Vanilla u otro)")
            print(" [5] Abrir archivo JSON interno en Bloc de notas")
            print(" [6] Abrir carpeta de datos de esta versión")
            print(" [V] Volver al menú")
            sub_opc = input("\nOpción: ").strip().lower()

            if sub_opc == "v" or not sub_opc: break
            elif sub_opc == "1":
                copy_to_clipboard(DEFAULT_MC_DIR)
                cand = os.path.abspath(input("\nPega la nueva ruta (o 'v'): ").strip().strip('"'))
                if cand.lower() == "v": continue
                if input(f"\n¿Mudar directorio a:\n\"{cand}\"? (s/n): ").strip().lower() == "s":
                    os.makedirs(cand, exist_ok=True)
                    config.setdefault("version_dirs", {})[target] = cand
                    save_config(config)
                    current_game_dir = cand
            elif sub_opc == "2":
                new_name = input("Nuevo nombre: ").strip()
                if new_name and new_name not in vers:
                    new_dir = os.path.join(DEFAULT_MC_DIR, "versions", new_name)
                    os.rename(target_dir, new_dir)
                    old_json = os.path.join(new_dir, f"{target}.json")
                    new_json = os.path.join(new_dir, f"{new_name}.json")
                    if os.path.exists(old_json):
                        with open(old_json, "r", encoding="utf-8") as f: data = json.load(f)
                        data["id"] = new_name
                        with open(new_json, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)
                        if old_json != new_json: os.remove(old_json)
                    old_jar = os.path.join(new_dir, f"{target}.jar")
                    new_jar = os.path.join(new_dir, f"{new_name}.jar")
                    if os.path.exists(old_jar): os.rename(old_jar, new_jar)
                    config.setdefault("version_dirs", {})[new_name] = config.get("version_dirs", {}).pop(target, DEFAULT_MC_DIR)
                    save_config(config)
                    target = new_name; target_dir = new_dir; json_file = new_json
            elif sub_opc == "3":
                version_content_manager(target, current_game_dir)
            elif sub_opc == "4":
                clear_screen()
                print("================================================================")
                print(" [!] ADVERTENCIA CRÍTICA: Instalar un modloader sobre una versión")
                print(" existente podría sobreescribir las librerías o desconfigurar.")
                print("================================================================")
                if input("¿Asumir el riesgo y continuar? (s/n): ").strip().lower() != "s": continue
                
                base_mc = extract_base_version(target)
                s = check_loader_support(base_mc)
                print(f"\nVersión base original extraída: {base_mc}")
                print(f" [1] NeoForge {'(Soportado)' if s['neoforge'] else '(Incompatible)'}")
                print(f" [2] Forge {'(Soportado)' if s['forge'] else '(Incompatible)'}")
                print(f" [3] Fabric {'(Soportado)' if s['fabric'] else '(Incompatible)'}")
                print(" [V] Cancelar")
                
                l_opt = input("\nElige cargador: ").strip().lower()
                if l_opt == "v": continue

                new_ver_id = None
                j_exec = ensure_java(get_required_java(base_mc))
                
                if l_opt == "1" and s["neoforge"]: new_ver_id = install_neoforge(base_mc, j_exec)
                elif l_opt == "2" and s["forge"]: new_ver_id = install_forge(base_mc, j_exec)
                elif l_opt == "3" and s["fabric"]: new_ver_id = install_fabric(base_mc)

                if new_ver_id:
                    config.setdefault("version_dirs", {})[new_ver_id] = current_game_dir
                    save_config(config)
                    target = new_ver_id
                    target_dir = os.path.join(DEFAULT_MC_DIR, "versions", target)
                    json_file = os.path.join(target_dir, f"{target}.json")
                    print(f"\n[OK] Modloader inyectado como: {new_ver_id}")
                    print(f"     Se conservó el directorio de datos: {current_game_dir}")
                else:
                    print("\n[X] La inyección del cargador ha fallado.")
                input("\nPresiona ENTER para continuar...")
            elif sub_opc == "5":
                if os.path.exists(json_file): os.system(f'notepad.exe "{json_file}"')
            elif sub_opc == "6":
                try: os.startfile(current_game_dir)
                except AttributeError: pass
# =====================================================================
# 9. PROTECCIÓN DE MEMORIA Y ARGUMENTOS (JVM)
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
    except Exception: pass

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
    if mode == "adaptive": return [f"-Xmx{calculate_adaptive_ram(version_id, game_dir)}G"] + base_flags
    elif mode == "auto": return [f"-Xmx{int(max(2, min(int(avail_ram - 1), int(total_ram * 0.85))))}G"] + base_flags
    elif mode.startswith("slot_"): return [f"-Xmx{config.get('ram_slots', {}).get(mode.split('_')[1], '4G')}"] + base_flags
    return ["-Xmx4G"] + base_flags

def manage_ram():
    while True:
        clear_screen()
        config = load_config()
        total_ram, avail_ram, used_pct = get_system_ram()
        mode = config.get("ram_mode", "adaptive")
        slots = config.get("ram_slots", {})
        print("================================================================")
        print("          PERSONALIZACIÓN DE MEMORIA RAM                        ")
        print("================================================================")
        print(f" RAM: {total_ram} GB | LIBRE: {avail_ram} GB ({used_pct}% en uso)")
        print(f" Modo activo: {mode.upper()}")
        print("----------------------------------------------------------------")
        print(" [D] Adaptativo Inteligente   [A] Automático Estándar")
        print(" [S] Seleccionar Slot manual  [E] Editar Slot")
        print(" [V] Volver")
        for i in range(1, 6):
            mark = "-> ACTIVO" if (mode.startswith("slot_") and config.get("active_ram_slot") == str(i)) else ""
            print(f"  Slot [{i}]: {slots.get(str(i), '4G')} {mark}")
        opc = input("Selecciona: ").strip().lower()
        if opc == "v": break
        elif opc == "d": config["ram_mode"] = "adaptive"; save_config(config)
        elif opc == "a": config["ram_mode"] = "auto"; save_config(config)
        elif opc == "s":
            s_num = input("Slot a activar (1-5): ").strip()
            if s_num in ["1", "2", "3", "4", "5"]:
                config["ram_mode"] = f"slot_{s_num}"; config["active_ram_slot"] = s_num; save_config(config)
        elif opc == "e":
            s_num = input("Slot a editar (1-5): ").strip()
            if s_num in ["1", "2", "3", "4", "5"]:
                val = input("Cantidad en GB a asignar (ej. 4): ").strip()
                if val.isdigit():
                    if int(val) > total_ram * 0.85:
                        print("Excede límite seguro."); input(); continue
                    slots[s_num] = f"{val}G"; config["ram_slots"] = slots; save_config(config)

def manage_aspect_ratio_test():
    clear_screen()
    config = load_config()
    disp = config.get("display", {"width": 854, "height": 480, "aspect_ratio": "16:9"})
    print("================================================================")
    print("            RELACIÓN DE ASPECTO                                 ")
    print("================================================================")
    print(" [1] 16:9 Estándar (854 x 480)")
    print(" [2] 16:9 Full HD   (1920 x 1080)")
    print(" [3] 4:3  Clásico   (1024 x 768)")
    print(" [4] 16:10 Laptop   (1280 x 800)")
    print(" [V] Cancelar y volver")
    opc = input("Selecciona opción: ").strip().lower()
    resolutions = {"1": (854, 480, "16:9"), "2": (1920, 1080, "16:9"), "3": (1024, 768, "4:3"), "4": (1280, 800, "16:10")}
    if opc in resolutions:
        new_w, new_h, new_ratio = resolutions[opc]
        disp["width"] = new_w; disp["height"] = new_h; disp["aspect_ratio"] = new_ratio
        config["display"] = disp; save_config(config)
        print("Guardado."); input()

def experimental_menu():
    while True:
        clear_screen()
        print("================================================================")
        print("             PANEL EXPERIMENTAL DE RENDIMIENTO                  ")
        print("================================================================")
        print(" [1] Guía explicativa")
        print(" [2] Personalización de RAM")
        print(" [3] Resolución y aspecto")
        print(" [V] Volver")
        opc = input("Selecciona: ").strip().lower()
        if opc == "1": show_novice_guide()
        elif opc == "2": manage_ram()
        elif opc == "3": manage_aspect_ratio_test()
        elif opc == "v": break

# =====================================================================
# 10. ACCESO DIRECTO RÁPIDO
# =====================================================================
def create_custom_desktop_shortcut():
    clear_screen()
    print("================================================================")
    print("         CREAR ACCESO DIRECTO RÁPIDO AL ESCRITORIO              ")
    print("================================================================")
    vers = get_installed_list()
    if not vers:
        print("[!] No hay versiones instaladas."); input(); return
    for idx, v in enumerate(vers, 1): print(f" [{idx}] {v}")
    v_opc = input("Elige version: ").strip()
    if not v_opc or v_opc.lower() == "v": return
    version = vers[int(v_opc) - 1] if v_opc.isdigit() and 1 <= int(v_opc) <= len(vers) else v_opc
    if version not in vers: return

    config = load_config()
    active_users = [(k, v) for k, v in config.get("user_slots", {}).items() if v.strip()]
    if not active_users: print("[!] Registra un usuario primero."); input(); return

    for idx, (s_idx, uname) in enumerate(active_users, 1): print(f" [{idx}] {uname}")
    u_idx = input("Selecciona usuario: ").strip()
    if not u_idx.isdigit() or not (1 <= int(u_idx) <= len(active_users)): return
    chosen_user = active_users[int(u_idx) - 1][1]

    chosen_skin_slot = "default"
    ram_preset = "adaptive"
    
    desktop_path = get_real_desktop_path()
    os.makedirs(desktop_path, exist_ok=True)
    clean_ver = re.sub(r'[\\/*?:"<>|]', "", version)
    clean_user = re.sub(r'[\\/*?:"<>|]', "", chosen_user)
    shortcut_path = os.path.join(desktop_path, f"Minecraft {clean_ver} ({clean_user}).lnk")
    target_bat = os.path.join(SCRIPT_DIR, "Jugar.bat")

    vbs_script = os.path.join(DEFAULT_MC_DIR, "make_shortcut.vbs")
    icon_file = ensure_minecraft_icon() or ""
    
    vbs = []
    vbs.append('Set oWS = WScript.CreateObject("WScript.Shell")')
    vbs.append(f'sLinkFile = "{shortcut_path}"')
    vbs.append('Set oLink = oWS.CreateShortcut(sLinkFile)')
    vbs.append(f'oLink.TargetPath = "{target_bat}"')
    # Evitando comillas triples en cadena formateada que rompieron el parser antes
    vbs.append('oLink.Arguments = "--quick-launch ""' + version + '"" ""' + chosen_user + '"" ""' + chosen_skin_slot + '"" ""' + ram_preset + '"""')
    vbs.append(f'oLink.WorkingDirectory = "{SCRIPT_DIR}"')
    if icon_file and os.path.exists(icon_file): vbs.append(f'oLink.IconLocation = "{icon_file}, 0"')
    vbs.append('oLink.Save')

    try:
        with open(vbs_script, "w", encoding="latin-1") as f: f.write("\n".join(vbs))
        subprocess.run(["cscript", "//nologo", vbs_script], check=True)
        if os.path.exists(vbs_script): os.remove(vbs_script)
        print("[OK] Acceso directo creado."); input()
    except Exception as e: print(f"[ERROR] {e}"); input()

# =====================================================================
# 11. MOTOR DE EJECUCIÓN (ANTI-CRASH) Y LOGIN OAUTH2 (PREMIUM)
# =====================================================================
def login_microsoft_oauth():
    clear_screen()
    print("================================================================")
    print("            INICIO DE SESIÓN OFICIAL CON MICROSOFT              ")
    print("================================================================")
    print(" 1. Se abrirá el navegador de Windows.")
    print(" 2. Inicia sesión con tu cuenta comprada de Minecraft.")
    print(" 3. Vuelve a esta consola al terminar.")
    try:
        account = minecraft_launcher_lib.microsoft_account.login_with_msa()
        return account
    except Exception as e:
        print(f"\n[X] Error: {e}"); return None

def run_game_via_bat(command, game_dir, version):
    bat_path = os.path.join(game_dir, "run_minecraft_diagnostic.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write("title Minecraft Diagnostic Console\n")
        f.write("echo ========================================================\n")
        f.write(f"echo  Iniciando Minecraft {version}...\n")
        f.write("echo  Si se crashea, el error rojo quedara pausado abajo.\n")
        f.write("echo ========================================================\n")
        f.write(f"cd /d \"{game_dir}\"\n\n")
        
        cmd_str = []
        for arg in command:
            if " " in arg or "=" in arg or ";" in arg: cmd_str.append(f'"{arg}"')
            else: cmd_str.append(arg)
                
        f.write(" ".join(cmd_str) + "\n\n")
        f.write("echo [!] Proceso finalizado. El juego se cerro.\npause\n")
        
    try:
        subprocess.Popen(f'start "Minecraft Console" cmd /c "{bat_path}"', cwd=game_dir, shell=True)
        return True
    except Exception as e:
        print(f"[X] Fallo la consola de diagnostico: {e}"); return False

def launch_game_direct(version, username, skin_slot, ram_mode):
    config = load_config()
    game_dir = config.get("version_dirs", {}).get(version, DEFAULT_MC_DIR)
    java_exec = ensure_java(get_required_java(version))
    if java_exec.endswith("javaw.exe"): java_exec = java_exec.replace("javaw.exe", "java.exe")

    jvm_args = get_jvm_ram_args(version, game_dir, ram_mode)
    disp = config.get("display", {"width": 854, "height": 480})
    ms_acc = config.get("microsoft_accounts", {})
    
    if username in ms_acc:
        options = {"username": ms_acc[username]["name"], "uuid": ms_acc[username]["id"], "token": ms_acc[username]["access_token"]}
    else:
        options = {"username": username, "uuid": str(uuid.uuid3(uuid.NAMESPACE_DNS, username)).replace("-",""), "token": ""}

    options.update({"executablePath": java_exec, "gameDirectory": game_dir, "jvmArguments": jvm_args, "customResolution": True, "resolutionWidth": str(disp["width"]), "resolutionHeight": str(disp["height"])})
    
    cmd = minecraft_launcher_lib.command.get_minecraft_command(version, DEFAULT_MC_DIR, options)
    cl_cmd = [a for a in cmd if a not in ("--demo", "${clientid}")]
    run_game_via_bat(cl_cmd, game_dir, version)
    sys.exit(0)

def launch_game_flow():
    config = load_config()
    active_users = [(k, v) for k, v in config.get("user_slots", {}).items() if v.strip()]
    ms_acc = config.get("microsoft_accounts", {})
    
    if not active_users and not ms_acc:
        print(" No tienes cuentas configuradas. Ve a Personalización."); input(); return

    vers = get_installed_list()
    if not vers: print(" No hay versiones instaladas."); input(); return
    for idx, v in enumerate(vers, 1): print(f" [{idx}] {v}")
    opc = input("Elige la versión: ").strip()
    if not opc.isdigit() or int(opc) > len(vers): return
    version = vers[int(opc) - 1]

    print("Cuentas disponibles:")
    c = 1; user_map = {}
    if ms_acc:
        print(" --- Premium ---")
        for u in ms_acc.keys():
            user_map[str(c)] = ("Premium", u); print(f" [{c}] {u} [Premium]"); c+=1
    if active_users:
        print(" --- Offline ---")
        for slot, uname in active_users:
            if uname not in ms_acc: user_map[str(c)] = ("Offline", uname); print(f" [{c}] {uname}"); c+=1

    u_opc = input("Selecciona: ").strip()
    if u_opc not in user_map: return
    tipo, username = user_map[u_opc]

    options = {}
    if tipo == "Premium":
        data = ms_acc[username]
        options = {"username": data["name"], "uuid": data["id"], "token": data["access_token"]}
    else:
        options = {"username": username, "uuid": str(uuid.uuid3(uuid.NAMESPACE_DNS, username)).replace("-",""), "token": ""}

    game_dir = config.get("version_dirs", {}).get(version, DEFAULT_MC_DIR)
    java_exec = ensure_java(get_required_java(version))
    if java_exec.endswith("javaw.exe"): java_exec = java_exec.replace("javaw.exe", "java.exe")

    disp = config.get("display", {"width": 854, "height": 480})
    options.update({"executablePath": java_exec, "gameDirectory": game_dir, "jvmArguments": get_jvm_ram_args(version, game_dir), "customResolution": True, "resolutionWidth": str(disp["width"]), "resolutionHeight": str(disp["height"])})

    try:
        cmd = minecraft_launcher_lib.command.get_minecraft_command(version, DEFAULT_MC_DIR, options)
    except Exception as e: print(f"Error generando comando: {e}"); input(); return

    cl_cmd = []
    skip = False
    for arg in cmd:
        if skip: skip = False; continue
        if arg in ("--demo", "${clientid}", "${auth_session}"): continue
        if arg in ("--clientId", "--clientid") and tipo == "Offline": skip = True; continue
        cl_cmd.append(arg)

    print("Arrancando..."); run_game_via_bat(cl_cmd, game_dir, version); input()

# =====================================================================
# 12. MENÚS Y PERSONALIZACIÓN DE CUENTAS
# =====================================================================
def manage_microsoft_accounts():
    config = load_config()
    ms_acc = config.get("microsoft_accounts", {})
    while True:
        clear_screen()
        print("========================================")
        print("   ADMINISTRADOR DE CUENTAS MICROSOFT   ")
        print("========================================")
        for idx, u in enumerate(ms_acc.keys(), 1): print(f" [{idx}] {u}")
        print(" [A] Agregar Microsoft  [B] Borrar  [V] Volver")
        opc = input("Opcion: ").strip().lower()
        if opc == "v": break
        elif opc == "a":
            acc = login_microsoft_oauth()
            if acc and "name" in acc:
                ms_acc[acc["name"]] = acc; config["microsoft_accounts"] = ms_acc; save_config(config)
                print(f"Cuenta {acc['name']} vinculada exitosamente."); input()
        elif opc == "b":
            u = input("Nombre a borrar: ").strip()
            if u in ms_acc: del ms_acc[u]; config["microsoft_accounts"] = ms_acc; save_config(config)

def game_settings_menu():
    while True:
        clear_screen()
        print("========================================")
        print("       CONFIGURACIÓN DEL JUEGO          ")
        print("========================================")
        print(" [1] Instalar nueva versión")
        print(" [2] Ver instaladas")
        print(" [3] Crear acceso directo")
        print(" [4] Editar versión")
        print(" [5] Borrar versión")
        print(" [6] Abrir .minecraft")
        print(" [7] Experimental")
        print(" [8] Administrar Datos (Zip)")
        print(" [V] Volver")
        opc = input("Selecciona: ").strip().lower()
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
        print(" [1] Nombres Offline")
        print(" [2] Skins Offline")
        print(" [3] Cuentas Microsoft (Premium)")
        print(" [V] Volver")
        opc = input("Selecciona: ").strip().lower()
        if opc == "1": manage_users()
        elif opc == "2": manage_skins()
        elif opc == "3": manage_microsoft_accounts()
        elif opc == "v": break

def manage_users():
    config = load_config()
    slots = config.get("user_slots", {str(i): "" for i in range(1, 11)})
    while True:
        clear_screen()
        for i in range(1, 11): print(f" [{i}/10] {slots.get(str(i), '')}")
        print(" [E] Editar  [B] Borrar  [V] Volver")
        opc = input("Opción: ").strip().lower()
        if opc == "v": break
        elif opc == "e":
            s = input("Slot: ").strip()
            if s in slots: slots[s] = input("Nombre: "); config["user_slots"] = slots; save_config(config)
        elif opc == "b":
            s = input("Slot: ").strip()
            if s in slots: slots[s] = ""; config["user_slots"] = slots; save_config(config)

def manage_skins():
    os.makedirs(SKINS_DIR, exist_ok=True)
    config = load_config()
    slots = config.get("skin_slots", {})
    while True:
        clear_screen()
        for i in range(1, 11): print(f" [{i}/10] {slots.get(str(i), {'name': 'Vacio'})['name']}")
        print(" [E] Subir  [B] Borrar  [V] Volver")
        opc = input("Opción: ").strip().lower()
        if opc == "v": break
        elif opc == "e":
            s = input("Slot: ").strip()
            ruta = input("Ruta .png: ").strip().strip('"')
            if os.path.exists(ruta):
                dest = os.path.join(SKINS_DIR, f"slot_{s}.png"); shutil.copyfile(ruta, dest)
                slots[s] = {"name": f"Skin_{s}", "path": dest}; config["skin_slots"] = slots; save_config(config)

def show_info():
    clear_screen()
    print("================================================================")
    print("                  INFORMACIÓN LEGAL Y DESCARGOS                 ")
    print("================================================================")
    print(" Autor del launcher: MeqqFranz")
    print(" Estado del software: BETA (En desarrollo continuo)")
    print(" Lenguaje base: Python / Batch Script")
    print("----------------------------------------------------------------")
    print(" AVISO IMPORTANTE:")
    print(" Este launcher experimental ofrece funciones limitadas.")
    print(" Para servidores oficiales y soporte, te recomendamos adquirir")
    print(" la version oficial en: >> https://www.minecraft.net <<")
    print("----------------------------------------------------------------")
    print(" DESCARGO DE RESPONSABILIDAD:")
    print(" Este proyecto es independiente y NO esta afiliado, respaldado,")
    print(" asociado ni vinculado oficialmente con Microsoft Corporation (C),")
    print(" Mojang Studios (C) ni ninguna de sus filiales.")
    print(" Minecraft es una marca registrada de Mojang AB / Microsoft.")
    print("================================================================")
    input("Presiona ENTER para volver...")

def manage_data_backup():
    while True:
        clear_screen()
        print(" [1] Exportar Todo  [2] Importar  [V] Volver")
        opc = input("Opcion: ").strip().lower()
        if opc == "v": break
        elif opc == "1":
            zip_path = os.path.join(get_real_desktop_path(), f"Backup_{datetime.now().strftime('%Y%m%d%H%M')}.zip")
            with zipfile.ZipFile(zip_path, 'w') as zf:
                if os.path.exists(CONFIG_FILE): zf.write(CONFIG_FILE, "launcher_config.json")
            print("Backup creado en Escritorio."); input()
        elif opc == "2":
            zp = input("Ruta del zip: ").strip().strip('"')
            if os.path.exists(zp):
                with zipfile.ZipFile(zp, 'r') as zf: zf.extractall(DEFAULT_MC_DIR)
                print("Restaurado."); input()

# =====================================================================
# 13. BUCLE PRINCIPAL
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
        print(" [2] Configuracion del juego")
        print(" [3] Personalizacion")
        print(" [4] Informacion y Descargos")
        print(" [5] Salir")
        opc = input("Selecciona: ").strip()
        if opc == "1": launch_game_flow()
        elif opc == "2": game_settings_menu()
        elif opc == "3": customization_menu()
        elif opc == "4": show_info()
        elif opc == "5": break

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--quick-launch":
        try: launch_game_direct(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        except Exception: main_menu()
    else: main_menu()