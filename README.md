# codex1

CLI en Python para administrar VMs con `libvirt`/`kvm`/`qemu` y convertir imágenes entre `OVA` y `QCOW2`.

## Requisitos

- Linux (probado para Ubuntu 25.10)
- `python3` (3.10+ recomendado)
- `virsh`
- `qemu-img`
- Permisos para administrar libvirt (normalmente grupo `libvirt` o root)

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

### Recordatorio de rutas para conversiones automáticas

Después de una conversión, el programa guarda rutas en SQLite (`~/.local/share/vm_manager/paths.db`).
Puedes consultarlas con:

```bash
python3 vm_manager.py paths
```

También puedes relanzar conversiones omitiendo `--ova`, `--qcow2` o `--out` si ya hay una ruta guardada.
