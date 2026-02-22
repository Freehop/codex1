# codex1

CLI en Python para administrar VMs con `libvirt`/`kvm`/`qemu`, convertir imágenes entre `OVA` y `QCOW2`, y aplicar compatibilidad de red `ensX`/`enp0X`.

## Requisitos

- Linux (objetivo Ubuntu 25.10 + kernel 6.17.0-14-generic)
- `python3` (3.10+ recomendado)
- `virsh`
- `qemu-img`
- Permisos para administrar libvirt y para editar `/etc/hosts` o `/etc/netplan`

## Uso rápido

```bash
python3 vm_manager.py --help
```

### Ver máquinas virtuales

```bash
python3 vm_manager.py list
```

### Ver sistema operativo y detalles de una VM

```bash
python3 vm_manager.py os NOMBRE_VM
```

### Validar configuración XML y ver errores

```bash
python3 vm_manager.py validate NOMBRE_VM
```

### Borrar VM

```bash
python3 vm_manager.py delete NOMBRE_VM
python3 vm_manager.py delete NOMBRE_VM --remove-storage
```

### Convertir OVA -> QCOW2

```bash
python3 vm_manager.py ova-to-qcow2 --ova /ruta/maquina.ova --out /ruta/salida.qcow2
```

### Convertir QCOW2 -> OVA

```bash
python3 vm_manager.py qcow2-to-ova --qcow2 /ruta/disco.qcow2 --out /ruta/export.ova --name vm-exportada
```

### Compatibilidad de interfaz entre OVA y QCOW2 (v2)

Esta función permite:
- **Debian**: agregar un bloque extra en `/etc/hosts` para alias `enp0X` y `ensX`.
- **Ubuntu**: crear un archivo con nombre aleatorio en `/etc/netplan/99-vmcompat-XXXXXX.yaml`.

Puedes elegir DHCP o estático e indicar el índice típico de interfaz (`3`, `9`, etc.).

```bash
# Debian + DHCP
python3 vm_manager.py network-compat --distro debian --mode dhcp --index 3

# Ubuntu + DHCP
python3 vm_manager.py network-compat --distro ubuntu --mode dhcp --index 3

# Ubuntu + estático
python3 vm_manager.py network-compat \
  --distro ubuntu \
  --mode static \
  --index 9 \
  --ip-cidr 192.168.122.50/24 \
  --gateway 192.168.122.1 \
  --dns 1.1.1.1,8.8.8.8
```

### Recordatorio de rutas para conversiones automáticas

Después de conversiones (y algunos ajustes automáticos), el programa guarda rutas en SQLite (`~/.local/share/vm_manager/paths.db`).

```bash
python3 vm_manager.py paths
```

Puedes relanzar conversiones omitiendo `--ova`, `--qcow2` o `--out` si ya hay una ruta guardada.
