#!/usr/bin/env python3
"""Herramienta CLI para administrar VMs con libvirt/KVM y convertir OVA<->QCOW2."""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import subprocess
import tarfile
import tempfile
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

APP_DIR = Path.home() / ".local" / "share" / "vm_manager"
DB_PATH = APP_DIR / "paths.db"


class CommandError(RuntimeError):
    """Error al ejecutar un comando externo."""


@dataclass
class CommandResult:
    stdout: str
    stderr: str


class PathStore:
    """Persistencia local de rutas recientes para conversiones automáticas."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paths (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    def set(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO paths (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        self.conn.commit()

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM paths WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def all(self) -> list[tuple[str, str, str]]:
        rows = self.conn.execute(
            "SELECT key, value, updated_at FROM paths ORDER BY updated_at DESC"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


class VMManager:
    def __init__(self, path_store: PathStore):
        self.path_store = path_store

    def _run(self, command: Iterable[str]) -> CommandResult:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise CommandError(
                f"Comando falló: {' '.join(command)}\n{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return CommandResult(stdout=proc.stdout, stderr=proc.stderr)

    def list_vms(self) -> list[dict[str, str]]:
        result = self._run(["virsh", "list", "--all", "--name"])
        vm_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        vms = []
        for name in vm_names:
            info = self._run(["virsh", "dominfo", name]).stdout
            info_dict = {}
            for line in info.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    info_dict[key.strip()] = value.strip()

            os_type = info_dict.get("OS Type", "desconocido")
            state = info_dict.get("State", "desconocido")
            vms.append({"name": name, "state": state, "os_type": os_type})
        return vms

    def delete_vm(self, name: str, remove_storage: bool = False) -> None:
        try:
            state = self._run(["virsh", "domstate", name]).stdout.strip().lower()
            if "running" in state:
                self._run(["virsh", "destroy", name])
        except CommandError:
            pass

        cmd = ["virsh", "undefine", name, "--nvram"]
        if remove_storage:
            cmd.append("--remove-all-storage")
        self._run(cmd)

    def os_details(self, name: str) -> dict[str, str]:
        dominfo = self._run(["virsh", "dominfo", name]).stdout
        dumpxml = self._run(["virsh", "dumpxml", name]).stdout

        info = {"name": name}
        for line in dominfo.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                info[key.strip()] = value.strip()

        os_type, arch = "desconocido", "desconocido"
        for line in dumpxml.splitlines():
            line = line.strip()
            if line.startswith("<type") and "</type>" in line:
                os_type = line.split(">", 1)[1].split("</type>")[0]
                if "arch=\"" in line:
                    arch = line.split("arch=\"", 1)[1].split("\"", 1)[0]
                break

        info["Guest OS"] = os_type
        info["Architecture"] = arch
        return info

    def validate_vm_config(self, name: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            xml_path = Path(tmpdir) / f"{name}.xml"
            xml = self._run(["virsh", "dumpxml", name]).stdout
            xml_path.write_text(xml, encoding="utf-8")

            proc = subprocess.run(
                ["virsh", "domxml-validate", str(xml_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        if proc.returncode == 0:
            return []

        return [line.strip() for line in (proc.stderr + "\n" + proc.stdout).splitlines() if line.strip()]

    def ova_to_qcow2(self, ova_path: Path, output_qcow2: Path) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with tarfile.open(ova_path, "r") as tar:
                tar.extractall(tmp)

            candidates = list(tmp.glob("*.vmdk")) + list(tmp.glob("*.qcow2")) + list(tmp.glob("*.img"))
            if not candidates:
                raise CommandError("No se encontró disco compatible dentro del archivo OVA")

            output_qcow2.parent.mkdir(parents=True, exist_ok=True)
            self._run(["qemu-img", "convert", "-O", "qcow2", str(candidates[0]), str(output_qcow2)])

        self.path_store.set("last_ova_input", str(ova_path.resolve()))
        self.path_store.set("last_qcow2_output", str(output_qcow2.resolve()))

    def qcow2_to_ova(self, qcow2_path: Path, output_ova: Path, vm_name: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            vmdk_name = f"{vm_name}.vmdk"
            ovf_name = f"{vm_name}.ovf"
            vmdk_path = tmp / vmdk_name
            ovf_path = tmp / ovf_name

            self._run(
                [
                    "qemu-img",
                    "convert",
                    "-O",
                    "vmdk",
                    "-o",
                    "subformat=streamOptimized",
                    str(qcow2_path),
                    str(vmdk_path),
                ]
            )

            info = json.loads(self._run(["qemu-img", "info", "--output=json", str(qcow2_path)]).stdout)
            capacity = int(info.get("virtual-size", 0))
            disk_id = str(uuid.uuid4())

            ovf = textwrap.dedent(
                f"""\
                <?xml version="1.0" encoding="UTF-8"?>
                <Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
                          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
                  <References>
                    <File ovf:id="file1" ovf:href="{vmdk_name}" ovf:size="{vmdk_path.stat().st_size}"/>
                  </References>
                  <DiskSection>
                    <Info>Discos virtuales</Info>
                    <Disk ovf:diskId="{disk_id}" ovf:fileRef="file1" ovf:capacity="{capacity}" ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
                  </DiskSection>
                  <VirtualSystem ovf:id="{vm_name}">
                    <Info>Maquina virtual exportada</Info>
                    <OperatingSystemSection ovf:id="1">
                      <Info>Sistema operativo invitado</Info>
                    </OperatingSystemSection>
                  </VirtualSystem>
                </Envelope>
                """
            )
            ovf_path.write_text(ovf, encoding="utf-8")

            output_ova.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(output_ova, "w") as tar:
                tar.add(ovf_path, arcname=ovf_name)
                tar.add(vmdk_path, arcname=vmdk_name)

        self.path_store.set("last_qcow2_input", str(qcow2_path.resolve()))
        self.path_store.set("last_ova_output", str(output_ova.resolve()))

    def add_network_compat(self, distro: str, mode: str, index: int, ip_cidr: str | None, gateway: str | None,
                           dns: str | None, hosts_path: Path = Path("/etc/hosts"),
                           netplan_dir: Path = Path("/etc/netplan")) -> Path:
        interface_name = f"ens{index}"
        legacy_name = f"enp0{index}"

        if distro == "debian":
            snippet = textwrap.dedent(
                f"""
                # vm_manager compat ({mode})
                # Compatibilidad OVA/QCOW2: alias {legacy_name} -> {interface_name}
                127.0.1.1 {legacy_name}
                127.0.1.1 {interface_name}
                """
            ).strip() + "\n"
            if hosts_path.exists() and snippet in hosts_path.read_text(encoding="utf-8", errors="ignore"):
                return hosts_path
            with hosts_path.open("a", encoding="utf-8") as fh:
                fh.write("\n" + snippet)
            self.path_store.set("last_hosts_update", str(hosts_path))
            return hosts_path

        random_name = f"99-vmcompat-{secrets.token_hex(3)}.yaml"
        out = netplan_dir / random_name
        netplan_dir.mkdir(parents=True, exist_ok=True)

        dns_list = [x.strip() for x in (dns or "").split(",") if x.strip()]
        nameservers_yaml = ""
        if dns_list:
            nameservers_yaml = f"\n      nameservers:\n        addresses: [{', '.join(dns_list)}]"

        if mode == "dhcp":
            interface_yaml = "dhcp4: true"
        else:
            if not ip_cidr or not gateway:
                raise CommandError("Para modo static debes indicar --ip-cidr y --gateway")
            interface_yaml = (
                f"dhcp4: false\n      addresses: [{ip_cidr}]\n      routes:\n"
                f"        - to: default\n          via: {gateway}{nameservers_yaml}"
            )

        content = textwrap.dedent(
            f"""
            network:
              version: 2
              renderer: networkd
              ethernets:
                {interface_name}:
                  {interface_yaml}
                {legacy_name}:
                  dhcp4: true
                  optional: true
            """
        ).strip() + "\n"

        out.write_text(content, encoding="utf-8")
        self.path_store.set("last_netplan_file", str(out))
        return out


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Administrador de VMs con libvirt + convertidor OVA/QCOW2")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Lista VMs y estado")

    delete = sub.add_parser("delete", help="Elimina una VM")
    delete.add_argument("name", help="Nombre de la VM")
    delete.add_argument("--remove-storage", action="store_true", help="Eliminar también discos")

    os_cmd = sub.add_parser("os", help="Muestra detalles de SO de una VM")
    os_cmd.add_argument("name", help="Nombre de la VM")

    validate = sub.add_parser("validate", help="Valida XML de una VM y reporta errores")
    validate.add_argument("name", help="Nombre de la VM")

    to_qcow2 = sub.add_parser("ova-to-qcow2", help="Convierte OVA a QCOW2")
    to_qcow2.add_argument("--ova", help="Ruta del archivo OVA")
    to_qcow2.add_argument("--out", help="Ruta de salida QCOW2")

    to_ova = sub.add_parser("qcow2-to-ova", help="Convierte QCOW2 a OVA")
    to_ova.add_argument("--qcow2", help="Ruta del archivo QCOW2")
    to_ova.add_argument("--out", help="Ruta de salida OVA")
    to_ova.add_argument("--name", required=True, help="Nombre lógico de VM para OVF")

    compat = sub.add_parser("network-compat", help="Agrega compatibilidad ensX/enp0X para Debian/Ubuntu")
    compat.add_argument("--distro", choices=["debian", "ubuntu"], required=True)
    compat.add_argument("--mode", choices=["dhcp", "static"], required=True)
    compat.add_argument("--index", type=int, default=3, help="Índice de interfaz, normalmente 3, 9, etc.")
    compat.add_argument("--ip-cidr", help="IP/CIDR para static, ej 192.168.122.50/24")
    compat.add_argument("--gateway", help="Gateway para static")
    compat.add_argument("--dns", help="DNS separado por comas")

    sub.add_parser("paths", help="Muestra rutas guardadas para automatización")
    return parser


def resolve_with_saved(value: str | None, key: str, store: PathStore, hint: str) -> Path:
    if value:
        return Path(value)
    saved = store.get(key)
    if not saved:
        raise CommandError(f"No se indicó ruta y no hay valor guardado para {hint}")
    return Path(saved)


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()

    store = PathStore()
    manager = VMManager(store)

    try:
        if args.command == "list":
            vms = manager.list_vms()
            if not vms:
                print("No hay VMs registradas.")
            for vm in vms:
                print(f"{vm['name']}: estado={vm['state']} os={vm['os_type']}")

        elif args.command == "delete":
            manager.delete_vm(args.name, remove_storage=args.remove_storage)
            print(f"VM '{args.name}' eliminada.")

        elif args.command == "os":
            info = manager.os_details(args.name)
            for k, v in info.items():
                print(f"{k}: {v}")

        elif args.command == "validate":
            issues = manager.validate_vm_config(args.name)
            if not issues:
                print("Sin errores de configuración detectados por virsh domxml-validate.")
            else:
                print("Errores/advertencias detectados:")
                for issue in issues:
                    print(f"- {issue}")

        elif args.command == "ova-to-qcow2":
            ova_path = resolve_with_saved(args.ova, "last_ova_input", store, "entrada OVA")
            output_qcow2 = resolve_with_saved(args.out, "last_qcow2_output", store, "salida QCOW2")
            manager.ova_to_qcow2(ova_path, output_qcow2)
            print(f"Conversión completada: {ova_path} -> {output_qcow2}")

        elif args.command == "qcow2-to-ova":
            qcow2_path = resolve_with_saved(args.qcow2, "last_qcow2_input", store, "entrada QCOW2")
            output_ova = resolve_with_saved(args.out, "last_ova_output", store, "salida OVA")
            manager.qcow2_to_ova(qcow2_path, output_ova, args.name)
            print(f"Conversión completada: {qcow2_path} -> {output_ova}")

        elif args.command == "network-compat":
            changed = manager.add_network_compat(
                distro=args.distro,
                mode=args.mode,
                index=args.index,
                ip_cidr=args.ip_cidr,
                gateway=args.gateway,
                dns=args.dns,
            )
            print(f"Compatibilidad aplicada en: {changed}")

        elif args.command == "paths":
            data = store.all()
            if not data:
                print("No hay rutas guardadas todavía.")
            for key, value, updated_at in data:
                print(f"{key} = {value} (actualizado: {updated_at})")

    except CommandError as exc:
        print(f"ERROR: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
