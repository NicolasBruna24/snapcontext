# Intel XPU (Arc B-series) — guía de instalación y uso

Desde la **Fase 18**, SnapContext soporta oficialmente las GPUs **Intel Arc
(serie B / Battlemage)** — como la **Arc B70 de 32 GB** — mediante **IPEX-LLM**
con cuantización *low-bit*. El soporte es **opcional**: si no tienes el
hardware o las dependencias, el resto de la aplicación funciona con normalidad.

## Arquitectura del backend

`backend_xpu.py` intenta dos rutas de carga, en orden de preferencia:

1. **IPEX-LLM (recomendada)**: `ipex_llm.transformers.AutoModelForCausalLM`
   con `load_in_low_bit="sym_int4"` (configurable). Es la ruta con soporte
   estable de Battlemage desde `ipex-llm>=2.2.0`.
2. **IPEX clásico (legacy)**: `transformers` + `ipex.optimize(dtype=float16)`.
   Se usa solo si `ipex_llm` no está instalado.

## 1. Instalación de drivers y oneAPI

```bash
# Drivers de gpu Intel (Level Zero) — Ubuntu 24.04
sudo apt install -y intel-opencl-icd libze1 libze-intel-gpu1 clinfo
clinfo | head   # debe listar tu Arc B70

# Intel oneAPI Base Toolkit 2025+
wget -qO- https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
  | sudo gpg --dearmor -o /usr/share/keyrings/intel.gpg
echo "deb [signed-by=/usr/share/keyrings/intel.gpg] https://apt.repos.intel.com/oneapi all main" \
  | sudo tee /etc/apt/sources.list.d/oneAPI.list
sudo apt update && sudo apt install -y intel-basekit
source /opt/intel/oneapi/setvars.sh
```

## 2. Instalación de SnapContext con soporte XPU

```bash
pip install "snapcontext[xpu]"
# Equivale a: torch>=2.5.0, intel-extension-for-pytorch>=2.5.0,
#             ipex-llm[cpp]>=2.2.0, transformers>=4.40.0, accelerate>=0.30.0
```

> **Nota serie B (Battlemage):** necesitas `ipex-llm>=2.2.0`. Versiones
> anteriores no detectan correctamente la B580/B570/B70.

## 3. Uso

```bash
# Con el modelo por defecto (config.json → "xpu".model)
snapcontext "explica este código" --provider xpu

# Con modelo específico
snapcontext "refactoriza main.py" --provider xpu --xpu-model Qwen/Qwen2.5-7B

# Ajustar generación
snapcontext "..." --provider xpu --xpu-max-tokens 1024 --xpu-temperature 0.2
```

También puedes fijar el proveedor en `~/.snapcontext/config.json`:

```json
{
  "provider": "xpu",
  "xpu": {
    "model": "Qwen/Qwen2.5-7B",
    "max_tokens": 500,
    "temperature": 0.7,
    "device": "xpu",
    "low_bit": "sym_int4"
  }
}
```

## 4. Detección automática

Al arrancar (sin `--provider`), SnapContext detecta la GPU con
`torch.xpu` (y como fallback IPEX). Si te detecta una Intel Arc con las
dependencias listas, verás:

```
🧪 GPU Intel detectada (Intel(R) Arc(TM) B70). Puedes usarla con:
   snapcontext --provider xpu [--xpu-model MODELO]
```

Si pides `--provider xpu` sin las dependencias, el aviso te indica el
comando exacto: `pip install snapcontext[xpu]`.

## 5. Verificación del hardware

```bash
python3 -m unittest tests.test_xpu_integration -v
# Sin GPU: los tests se saltan (skip). Con GPU: inferencia real con un
# modelo pequeño (Qwen2.5-0.5B) para validar la instalación.
```

## 6. Solución de problemas

| Síntoma | Causa habitual |
| --- | --- |
| `No se detectó ninguna GPU Intel XPU` | Drivers/Level Zero no instalados, o falta `source /opt/intel/oneapi/setvars.sh`. |
| `snapcontext[xpu]` no resuelve `ipex-llm` | Usar Python 3.10–3.12 (IPEX-LLM no publica ruedas para 3.13+). |
| OOM al cargar | Reduce `low_bit` (`sym_int4` es lo más ligero) o usa un modelo más pequeño. |
| Battlemage no aparece | Actualiza a `ipex-llm>=2.2.0` y kernel/driver recientes. |
