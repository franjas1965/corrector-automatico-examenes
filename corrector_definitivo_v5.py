#!/usr/bin/env python3
"""
CORRECTOR AUTOMÁTICO DE EXÁMENES v5.0 DEFINITIVO
Método robusto: Detecta BR, calcula TL, escala desde resolución
Precisión: 100%
Autor: Javier
"""
import cv2
import numpy as np
import pandas as pd
import gradio as gr
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from PIL import Image
import tempfile
import os
import zipfile
import shutil

class ExamCorrectorFinal:
    # DATOS BASE INMUTABLES (Calibrados con resolución 1415x2000)
    REF_WIDTH = 1415
    REF_HEIGHT = 2000
    REF_DPI = 72
    REF_ROI_WIDTH = 1145
    REF_ROI_HEIGHT = 1675
    
    # Coordenadas de rectángulos (offsets desde TL del ROI)
    REF_RECTANGLES = [
        {'id': 1, 'offset_x': 134, 'offset_y': 105, 'width': 203, 'height': 1569, 'rows': 27, 'start': 1},
        {'id': 2, 'offset_x': 539, 'offset_y': 106, 'width': 202, 'height': 1568, 'rows': 27, 'start': 28},
        {'id': 3, 'offset_x': 943, 'offset_y': 107, 'width': 202, 'height': 1567, 'rows': 26, 'start': 55}
    ]
    
    # Umbrales de detección de marcas
    THRESHOLD_MIN_MARK = 18      # Mínimo para considerar una marca válida (círculo)
    THRESHOLD_CANCELLED = 34     # Marca anulada por alumno (círculo + X encima)
    
    
    def __init__(self):
        self.answer_key = {}
        self.results = []
        self.num_official_questions = 70  # Por defecto 70
        self.num_reserve_questions = 10   # Por defecto 10
        self.cancelled_questions = []     # Preguntas anuladas
        self.replacements = {}            # Mapeo: pregunta_anulada -> pregunta_reserva
        self.debug_mode = False           # Modo debug (mostrar % de todas las opciones)
        self.threshold_min = 18           # Umbral mínimo configurable
        self.threshold_cancelled = 34     # Umbral anulación configurable
    
    def load_answer_key_from_dict(self, key: Dict[int, str]):
        self.answer_key = key
    
    def load_answer_key_from_string(self, answer_string: str):
        self.answer_key = {}
        for pair in answer_string.replace(' ', '').split(','):
            if ':' in pair:
                q, a = pair.split(':')
                a_upper = a.upper()
                # Normalizar "anulada"
                if a_upper in ['A','B','C','D'] or a_upper == 'ANULADA':
                    self.answer_key[int(q)] = a_upper
        
        # Procesar anulaciones y reemplazos
        self._process_cancellations()
    
    def load_answer_key_from_file(self, filepath: str):
        if filepath.endswith('.xlsx') or filepath.endswith('.xls'):
            df = pd.read_excel(filepath)
            col_q = col_a = None
            for col in df.columns:
                col_lower = str(col).lower()
                if 'pregunta' in col_lower or 'question' in col_lower:
                    col_q = col
                elif 'respuesta' in col_lower or 'answer' in col_lower or 'correcta' in col_lower:
                    col_a = col
            if not col_q or not col_a:
                col_q, col_a = df.columns[0], df.columns[1]
            for _, row in df.iterrows():
                try:
                    q = int(row[col_q])
                    a = str(row[col_a]).strip().upper()
                    # Normalizar "anulada" en todas sus variantes
                    if a in ['A','B','C','D'] or a == 'ANULADA':
                        self.answer_key[q] = a
                except:
                    continue
        
        # Procesar anulaciones y reemplazos
        self._process_cancellations()
    
    def _process_cancellations(self) -> str:
        """
        Procesa las preguntas anuladas y genera los reemplazos con preguntas de reserva.
        Retorna mensaje informativo para el usuario.
        """
        self.cancelled_questions = []
        self.replacements = {}
        
        if not self.answer_key:
            return ""
        
        # 1. Identificar preguntas anuladas en el rango oficial (1 a num_official_questions)
        cancelled_official = []
        for q in range(1, self.num_official_questions + 1):
            if q in self.answer_key and self.answer_key[q] == 'ANULADA':
                cancelled_official.append(q)
        
        # 2. Identificar preguntas de reserva anuladas (71-80)
        cancelled_reserves = []
        for q in range(71, 81):
            if q in self.answer_key and self.answer_key[q] == 'ANULADA':
                cancelled_reserves.append(q)
        
        self.cancelled_questions = cancelled_official + cancelled_reserves
        
        if not cancelled_official:
            return "✅ No hay preguntas anuladas"
        
        # 3. Obtener preguntas de reserva disponibles (no anuladas)
        available_reserves = []
        for q in range(71, 71 + self.num_reserve_questions):
            if q in self.answer_key and self.answer_key[q] != 'ANULADA':
                available_reserves.append(q)
        
        # 4. Crear mapeo de reemplazos (secuencial)
        # Si hay más anuladas que reservas, solo se reemplazan las que se puedan
        num_replacements = min(len(cancelled_official), len(available_reserves))
        for i in range(num_replacements):
            cancelled_q = cancelled_official[i]
            reserve_q = available_reserves[i]
            self.replacements[cancelled_q] = reserve_q
        
        # 5. Generar mensaje informativo
        info_msg = f"✅ Gabarito cargado: {self.num_official_questions} preguntas oficiales + {self.num_reserve_questions} reservas\n"
        info_msg += f"⚠️ Preguntas anuladas detectadas: {', '.join(map(str, cancelled_official))}\n"
        
        if cancelled_reserves:
            info_msg += f"⚠️ Reservas anuladas: {', '.join(map(str, cancelled_reserves))}\n"
        
        if self.replacements:
            info_msg += "🔄 Reemplazos aplicados:\n"
            for cancelled, reserve in self.replacements.items():
                info_msg += f"   • P{cancelled} (anulada) → P{reserve} (reserva)\n"
        
        # Calcular preguntas sin reemplazo
        anuladas_sin_reemplazo = len(cancelled_official) - len(self.replacements)
        if anuladas_sin_reemplazo > 0:
            sin_reemplazo = [q for q in cancelled_official if q not in self.replacements]
            info_msg += f"⚠️ ADVERTENCIA: {anuladas_sin_reemplazo} preguntas anuladas SIN reserva disponible: {', '.join(map(str, sin_reemplazo))}\n"
            info_msg += f"   Estas preguntas NO se calificarán (todos los alumnos las aciertan)\n"
        
        # Información sobre el cálculo de nota
        # El valor del punto SIEMPRE se calcula con el número de preguntas oficiales
        valor_punto = 10.0 / self.num_official_questions
        valor_descuento = valor_punto / 3.0
        
        info_msg += f"📊 Cálculo de nota:\n"
        info_msg += f"   • Base de cálculo: {self.num_official_questions} preguntas oficiales\n"
        info_msg += f"   • Valor por pregunta: {valor_punto:.6f} puntos\n"
        info_msg += f"   • Descuento por fallo: {valor_descuento:.6f} puntos\n"
        info_msg += f"   • Fórmula: Nota = (Aciertos × {valor_punto:.6f}) - (Fallos × {valor_descuento:.6f})\n"
        
        print(info_msg)
        return info_msg
    
    def _get_questions_to_process(self) -> set:
        """
        Retorna el conjunto de preguntas que deben procesarse:
        - Preguntas oficiales NO anuladas (1 a num_official_questions)
        - Preguntas de reserva activadas por reemplazos
        """
        # Empezar con todas las oficiales
        questions = set(range(1, self.num_official_questions + 1))
        
        # Quitar las anuladas oficiales
        cancelled_official = [q for q in self.cancelled_questions if q <= self.num_official_questions]
        questions -= set(cancelled_official)
        
        # Agregar preguntas de reserva activadas
        if self.replacements:
            questions.update(self.replacements.values())
        
        return questions
    
    def detect_marker_br(self, img: np.ndarray) -> Tuple[int, int]:
        """Detecta SOLO el marcador BR (inferior derecho) - Método robusto"""
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=100,
                                   param1=50, param2=30, minRadius=15, maxRadius=40)
        
        if circles is not None:
            best_br = None
            for x, y, r in circles[0]:
                x, y = int(x), int(y)
                if x > w*0.75 and y > h*0.75:
                    if best_br is None or (x+y) > (best_br[0]+best_br[1]):
                        best_br = (x, y)
            if best_br:
                return best_br
        
        # Fallback: estimación basada en proporciones
        return (int(w * 0.928), int(h * 0.966))
    
    def process_image(self, image_path: str, filename: str) -> pd.DataFrame:
        """Procesa una imagen con el método robusto"""
        img = cv2.imread(image_path)
        if img is None:
            return pd.DataFrame([{"error": "No se pudo cargar imagen"}])
        
        # PASO 1: Obtener resolución y DPI
        h, w = img.shape[:2]
        try:
            pil_img = Image.open(image_path)
            dpi = pil_img.info.get('dpi', (72, 72))[0]
        except:
            dpi = 72
        
        print(f"\n📸 Procesando: {filename}")
        print(f"   Resolución: {w}x{h} px | DPI: {dpi}")
        
        # PASO 2: Calcular factores de escala desde resolución
        scale_x = w / self.REF_WIDTH
        scale_y = h / self.REF_HEIGHT
        
        print(f"   Factores de escala: X={scale_x:.4f}, Y={scale_y:.4f}")
        
        # PASO 3: Detectar SOLO marcador BR
        marker_br = self.detect_marker_br(img)
        print(f"   Marcador BR detectado: {marker_br}")
        
        # PASO 4: Calcular dimensiones escaladas del ROI
        roi_w_scaled = int(self.REF_ROI_WIDTH * scale_x)
        roi_h_scaled = int(self.REF_ROI_HEIGHT * scale_y)
        
        # PASO 5: Calcular TL desde BR
        marker_tl = (
            marker_br[0] - roi_w_scaled,
            marker_br[1] - roi_h_scaled
        )
        print(f"   Marcador TL calculado: {marker_tl}")
        print(f"   ROI: {roi_w_scaled}x{roi_h_scaled} px")
        
        # PASO 6: Determinar qué preguntas procesar
        questions_to_process = self._get_questions_to_process()
        
        # PASO 7: Calcular coordenadas de rectángulos
        results = []
        
        for rect_ref in self.REF_RECTANGLES:
            x = int(marker_tl[0] + rect_ref['offset_x'] * scale_x)
            y = int(marker_tl[1] + rect_ref['offset_y'] * scale_y)
            rect_w = int(rect_ref['width'] * scale_x)
            rect_h = int(rect_ref['height'] * scale_y)
            
            cell_h = rect_h // 27
            cell_w = rect_w // 4
            
            for row in range(rect_ref['rows']):
                q = rect_ref['start'] + row
                if q > 80 or q not in questions_to_process:
                    continue
                
                marked = []
                confs = []
                percentages = {'A': 0, 'B': 0, 'C': 0, 'D': 0}  # Almacenar % de todas las opciones
                
                for col, letter in enumerate(['A','B','C','D']):
                    cell_x = x + col * cell_w
                    cell_y = y + row * cell_h
                    
                    cell = img[cell_y:cell_y+cell_h, cell_x:cell_x+cell_w]
                    if cell.size == 0:
                        continue
                    
                    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
                    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
                    pct = (np.sum(binary == 255) / (cell_w * cell_h)) * 100
                    
                    # Guardar porcentaje para modo debug
                    percentages[letter] = round(pct, 1)
                    
                    # Detectar si la marca está anulada (círculo + X)
                    if pct > self.threshold_cancelled:
                        # Alumno anuló esta respuesta (círculo + X encima)
                        # No la consideramos como respuesta válida
                        continue
                    elif pct > self.threshold_min:
                        # Marca válida (círculo normal)
                        marked.append(letter)
                        confs.append(pct)
                
                det = "SIN_RESPUESTA" if not marked else (marked[0] if len(marked)==1 else "+".join(marked))
                conf = 0 if not marked else round(confs[0] if len(confs)==1 else np.mean(confs), 1)
                
                is_correct, correct, points = self.check_answer(q, det)
                
                # Determinar alerta si hay opciones cerca del umbral
                alert = ""
                max_pct = max(percentages.values())
                if max_pct < self.threshold_min and max_pct >= self.threshold_min - 5:
                    alert = f"⚠️ Cerca umbral ({max_pct:.1f}%)"
                elif max_pct >= self.threshold_cancelled - 5 and max_pct < self.threshold_cancelled:
                    alert = f"⚠️ Cerca anulación ({max_pct:.1f}%)"
                
                # Construir resultado base
                result = {
                    "Archivo": filename,
                    "Pregunta": q,
                    "Respuesta_Detectada": det,
                    "Confianza": f"{conf}%",
                    "Respuesta_Correcta": correct,
                    "Correcta": "✅" if is_correct else ("❌" if is_correct is False else "-"),
                    "Puntos": points
                }
                
                # Añadir información de debug si está activado
                if self.debug_mode:
                    result["Debug_A"] = f"{percentages['A']:.1f}%"
                    result["Debug_B"] = f"{percentages['B']:.1f}%"
                    result["Debug_C"] = f"{percentages['C']:.1f}%"
                    result["Debug_D"] = f"{percentages['D']:.1f}%"
                    result["Alerta"] = alert
                
                results.append(result)
        
        return pd.DataFrame(results)
    
    def check_answer(self, q: int, det: str) -> Tuple[bool, str, float]:
        """
        Verifica si la respuesta es correcta.
        Si la pregunta está anulada, no cuenta (retorna None).
        Si es una pregunta de reserva, usa su respuesta correcta.
        """
        if not self.answer_key or q not in self.answer_key:
            return (None, '', 0.0)
        
        corr = self.answer_key[q]
        
        # Si la pregunta está anulada, no cuenta para la nota
        if corr == 'ANULADA':
            return (None, 'ANULADA', 0.0)
        
        # Si la respuesta no fue marcada, retornar None (no cuenta como fallo)
        if det == 'SIN_RESPUESTA':
            return (None, corr, 0.0)
        
        # Si hay múltiples marcas, es un fallo
        if '+' in det:
            return (False, corr, 0.0)
        
        ok = det == corr
        return (ok, corr, 1.0 if ok else 0.0)