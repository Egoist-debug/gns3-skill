# Capability matrix

The live registry, not this document, owns the operation catalog, tier counts, summaries, and schemas:

```bash
gns3 list
gns3 list --tier=expert
gns3 list --tier=all
gns3 describe <operation>
```

This matrix classifies capability coverage and escape requirements. See [Safety](safety.md) before any destructive or non-CLI action.

## Legend

| Class | Meaning |
| --- | --- |
| **Green** | Fully supported through `gns3 run`; no escape is allowed or needed. |
| **Yellow** | The CLI covers part of the job, but a named gap may require the escape ritual. |
| **Red** | Not an agent operations path through this skill; use a human or a different authorized system. |

## Green — CLI only

| Job | Preferred route |
| --- | --- |
| Open or create a lab/project | `gns3 run prepare_lab`; discover expert project/session operations through the registry. |
| Build nodes and links | `gns3 run build_topology`; expert node/link/topology operations support custom convergence. |
| Configure device consoles | `gns3 run configure_devices`; expert console/template/bulk operations support custom work. |
| Diagnose topology/connectivity | `gns3 run diagnose_connectivity`; expert topology, console, and capture operations provide evidence. |
| Run guest shell commands | `gns3 run run_guest_commands`; `gns3 run ssh_exec` handles a custom guest path. |
| Import supported images and compute node Idle-PC | `gns3 run prepare_image`; expert image/list/Idle-PC operations cover the green subset. |
| Create/list/manage snapshots | `gns3 run manage_snapshot`; expert snapshot operations support explicit custom work. |
| Stop nodes, close a project, or stop a local server | `gns3 run finish_lab`; expert lifecycle operations require the same user consent. |
| Project lifecycle and archive export | Registry expert operations support project CRUD, duplicate, save, and export. |
| Node lifecycle | Registry expert operations support CRUD, start/stop, suspend, reload, duplicate, and bulk start/stop. |
| Packet capture | Registry expert operations support starting and stopping link capture. |
| Canvas annotations | Registry expert operations support text and shape annotations. |
| Inspect templates, appliances, images, computes, server, or topology | Registry expert list/get operations provide read-only discovery. |

Use `gns3 describe <operation>` for exact inputs. Goal and expert operation IDs are discoverable from `list`; the table intentionally does not duplicate all 58 entries.

## Yellow — CLI plus explicit escape

| Job | Green CLI portion | Gap | Minimum escape after approval |
| --- | --- | --- | --- |
| Create a Dynamips/IOS template | Import the image; list images and existing templates. | No registered template-create operation. | Create the template through an approved external path, or have a human use the GNS3 GUI. |
| Densify template slots | Update node instances; request densification through `gns3 run prepare_image` to receive the gap notice. | No registered template-update operation for persistent slot maps. | Update only the required template properties through an approved path. |
| Persist Idle-PC on a template | Compute candidate values on a running Dynamips node. | No registered operation writes the chosen value to a template record. | Apply the chosen value to the target template through an approved path. |
| Install an appliance file | List available appliances. | No registered appliance-file install operation. | Use the GNS3 GUI or another explicitly approved installation path. |
| Pull a Docker image | Inspect available resources; supported image import rejects Docker. | No registered Docker pull operation. | Use approved Docker tooling only when the lab requires it. |

Yellow means: name the exact gap, ask once for permission for that action, then perform only the minimum approved work. It is never blanket permission to bypass the CLI.

## Red — not skill agent operations

| Job | Reason |
| --- | --- |
| Drive a lab with raw REST, curl, or ad-hoc client scripts | Violates the CLI-only boundary. |
| Reverse-engineer or automate the GNS3 GUI | Outside this skill's operations interface. |
| Modify GNS3 server source or reinstall host OS packages | Human/infrastructure responsibility. |
| Operate physical networks or cloud VPCs | Requires a different authorized tool and safety model. |

## Authorization overlays

Capability color does not remove authorization:

- destructive goal actions use the persisted confirmation handshake;
- expert destructive/export/restore/stop/close actions require explicit user approval;
- cleanup requires the user's specific consent even though the operations are green;
- IDs and ports remain provenance-only;
- secrets remain sourced and handled according to [Safety](safety.md).

## Dynamips density and Idle-PC policy

For Dynamips image work:

- choose practical RAM/NVRAM and use sparse-memory/mmap features where applicable;
- fill supported slots with appropriate dense modules rather than leaving avoidable gaps;
- compute Idle-PC on a running node;
- persist density and Idle-PC on the template only through a registered operation, or through the yellow escape ritual after approval.

Image import and node-side Idle-PC computation are green. Template creation/update and template-side Idle-PC persistence remain yellow until they appear in the live registry.

## When a green operation fails

A hard failure does not silently reclassify a job as yellow. First fix preconditions through other green operations when possible. If the required operation remains broken, follow the escape ritual in [Safety](safety.md) with the exact operation and error.
