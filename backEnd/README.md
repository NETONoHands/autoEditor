# Tapa na Lata

## Backend de upload

Instale as dependências no ambiente virtual:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Inicie o servidor:

```powershell
& .\.venv\Scripts\python.exe upload_api.py
```

O servidor fica disponível em `http://127.0.0.1:8000`.

A partir da raiz do repositório, o módulo também pode ser iniciado com `backEnd\\.venv\\Scripts\\python.exe -m backEnd.upload_api`.

### Endpoint

`POST /api/uploads` recebe `multipart/form-data` com os campos obrigatórios:

- `video`: arquivo `.mp4`
- `subtitle`: arquivo `.srt`
- `title`: título não vazio

O upload aceito retorna HTTP `201` com um `upload_id`. Arquivos inválidos retornam `415` para extensão, `413` para tamanho e `422` para conteúdo inválido.

### Limites

Os limites são configuráveis por variáveis de ambiente:

- `TAPA_NA_LATA_MAX_VIDEO_BYTES`: padrão de 2 GiB
- `TAPA_NA_LATA_MAX_SRT_BYTES`: padrão de 16 MiB
- `TAPA_NA_LATA_UPLOAD_DIR`: diretório temporário dos uploads
- `TAPA_NA_LATA_HOST`: padrão `127.0.0.1`
- `TAPA_NA_LATA_PORT`: padrão `8000`

O endpoint `GET /health` retorna `{"status":"ok"}`.

### Projetos e edições

Um upload aceito cria automaticamente um `project_id`. A metadata pode ser consultada com `GET /api/projects/{project_id}/metadata`.

Para iniciar uma edição, envie `POST /api/projects/{project_id}/edits` com JSON opcional contendo `name`, `lut_path`, `remove_silence` e `silence_threshold`. A resposta é HTTP `202` e fornece um `edit_id`. O corte de silêncio usa `0,3` segundo por padrão quando `remove_silence` está habilitado.

Consulte o andamento em `GET /api/projects/{project_id}/edits/{edit_id}`. O estado informa `queued`, `running`, `completed` ou `failed`, além de `progress_percent` e `stage`. O resultado concluído inclui os intervalos de fala preservados e a duração validada do vídeo cortado.

Quando a edição estiver `completed`, liste os resultados em `GET /api/projects/{project_id}/edits/{edit_id}/outputs`. Baixe um arquivo usando `GET /api/projects/{project_id}/edits/{edit_id}/outputs/{output_id}/download` ou todos em `GET /api/projects/{project_id}/edits/{edit_id}/download.zip`.

Os diretórios dos projetos ficam em `TAPA_NA_LATA_UPLOAD_DIR` e são derivados dos identificadores gerados pelo servidor; caminhos locais não são aceitos pela API nem retornados nas respostas.

### Saída vertical

As edições geram uma variante vertical em `1080x1920`. O enquadramento começa com crop central e usa o posicionamento facial por amostragem quando `face_tracking` está habilitado, retornando ao centro se nenhum rosto for encontrado.

O título da edição é desenhado no topo em amarelo, com fonte DejaVu Sans Bold, tamanho 52 e contorno preto. As legendas ficam na região inferior, com margem própria, para evitar sobreposição com o título.
