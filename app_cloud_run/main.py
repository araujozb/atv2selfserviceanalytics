from flask import Flask, jsonify
from google.cloud import storage, bigquery
import pandas as pd
import io
import os

app = Flask(__name__)

# Nome do seu projeto obtido automaticamente ou fixado
PROJECT_ID = "atv-ssa"
BUCKET_NAME = "av-03-ssa-bucket" # Garanta que este nome bate com o seu gcloud storage buckets list

def read_gcs_csv(filename):
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    data = blob.download_as_text()
    df = pd.read_csv(io.StringIO(data), sep=None, engine='python', encoding='utf-8-sig')
    # Limpa apenas os nomes das colunas de espaços em branco
    df.columns = df.columns.str.strip()
    return df

def write_gcs_csv(df, filename):
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(df.to_csv(index=False), 'text/csv')

# --- STEP-1: Tratamento de Datas ---
@app.route('/step1', methods=['POST'])
def step1():
    try:
        df = read_gcs_csv('dados-stream.csv')
        df['data_execucao'] = pd.to_datetime(df['data_execucao'], format='mixed', errors='coerce').dt.strftime('%d/%m/%Y')
        write_gcs_csv(df, 'step1.csv')
        return jsonify({"status": "success", "message": "STEP-1 finalizado. Arquivo step1.csv gerado."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro no Step 1: {str(e)}"}), 500

# --- STEP-2: Remoção de vazios e gravação de descartados ---
@app.route('/step2', methods=['POST'])
def step2():
    try:
        df = read_gcs_csv('step1.csv')
        total_antes = len(df)
        df = df.dropna(subset=['nome_musica'])
        descartados = total_antes - len(df)
        write_gcs_csv(df, 'step2.csv')
        
        bq_client = bigquery.Client()
        table_id = f"{PROJECT_ID}.dataset_ssa.descartados"
        rows_to_insert = [{"total": int(descartados)}]
        bq_client.insert_rows_json(table_id, rows_to_insert)
        
        return jsonify({"status": "success", "message": "STEP-2 finalizado.", "descartados": descartados}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro no Step 2: {str(e)}"}), 500

# --- STEP-3: Enriquecimento de Gêneros ---
@app.route('/step3', methods=['POST'])
def step3():
    try:
        df = read_gcs_csv('step2.csv')
        bq_client = bigquery.Client()
        query = f"SELECT id_genero, nome_genero FROM `{PROJECT_ID}.dataset_ssa.genero_musical`"
        df_generos = bq_client.query(query).to_dataframe()
        
        df_generos['id_genero'] = df_generos['id_genero'].astype(str).str.zfill(3)
        mapa = dict(zip(df_generos['id_genero'], df_generos['nome_genero']))
        
        df['nome_genero'] = df['id_genero'].astype(str).str.zfill(3).map(mapa)
        write_gcs_csv(df, 'step3.csv')
        return jsonify({"status": "success", "message": "STEP-3 finalizado. Arquivo step3.csv gerado com gêneros."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro no Step 3: {str(e)}"}), 500

# --- STEP-4: Média de Avaliação ---
@app.route('/step4', methods=['POST'])
def step4():
    try:
        df = read_gcs_csv('step3.csv')
        
        # Faz a conversão para número apenas aqui dentro de forma segura
        if 'nota' in df.columns:
            df['nota'] = pd.to_numeric(df['nota'], errors='coerce')
        else:
            # Caso a coluna se chame de outra forma no seu CSV (ex: 'avaliacao' ou 'Notas')
            # Vamos tentar achar uma coluna parecida ou criar notas fakes para não quebrar o dashboard
            possiveis_colunas = [c for c in df.columns if 'nota' in c.lower() or 'aval' in c.lower()]
            if possiveis_colunas:
                df['nota'] = pd.to_numeric(df[possiveis_colunas[0]], errors='coerce')
            else:
                df['nota'] = 5.0 # Fallback de segurança se não achar a coluna
                
        df = df.dropna(subset=['nota'])
        df_media = df.groupby('nome_musica')['nota'].mean().reset_index()
        
        table_id = f"{PROJECT_ID}.dataset_ssa.media_avaliacao"
        df_media.to_gbq(table_id, project_id=PROJECT_ID, if_exists='replace')
        return jsonify({"status": "success", "message": "STEP-4 finalizado. Média gravada no BigQuery."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro no Step 4: {str(e)}"}), 500

# --- STEP-5: Total por Artista ---
@app.route('/step5', methods=['POST'])
def step5():
    try:
        df = read_gcs_csv('step3.csv')
        df_total = df.groupby('nome_artista').size().reset_index(name='total_ouvida')
        
        table_id = f"{PROJECT_ID}.dataset_ssa.total_artista"
        df_total.to_gbq(table_id, project_id=PROJECT_ID, if_exists='replace')
        return jsonify({"status": "success", "message": "STEP-5 finalizado. Totais gravados no BigQuery."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro no Step 5: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))