import json
import io
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel, Field
from typing import List, Optional
from google import genai
from google.genai import types

# ReportLab Imports for PDF
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfgen import canvas

app = FastAPI(title="Academic Timetable Generator")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- PYDANTIC SCHEMAS ---
class CellSession(BaseModel):
    subject: str = Field(description="Subject code or activity name")
    faculty: str = Field(description="Faculty initials or name")
    room: str = Field(description="Assigned Room/Lab code")
    group: Optional[str] = Field(None, description="Subgroup label e.g., S1, S2 or None")
    session_type: str = Field(description="Theory, Elective, Laboratory, Soft Skill, Break")


class SlotSchedule(BaseModel):
    time_slot: str = Field(description="Slot timing e.g., 09:00-10:00")
    is_break: bool = Field(False, description="True if this slot is a Break")
    sessions: List[CellSession] = Field(default_factory=list)


class ClassDaySchedule(BaseModel):
    class_division: str = Field(description="Class identifier e.g., SE AIML-A")
    slots: List[SlotSchedule]


class DailyMasterSchedule(BaseModel):
    day: str = Field(description="Day e.g., Monday")
    class_schedules: List[ClassDaySchedule]


class MasterTimetableResponse(BaseModel):
    institution_info: dict = Field(description="Metadata including institution, department")
    missing_inputs: List[str] = Field(default_factory=list)
    unmet_constraints: List[str] = Field(default_factory=list)
    timetable: List[DailyMasterSchedule]


def to_gemini_schema(pydantic_model):
    raw_schema = pydantic_model.model_json_schema()
    defs = raw_schema.pop("$defs", {})

    def resolve_node(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_key = node["$ref"].split("/")[-1]
                return resolve_node(defs.get(ref_key, {}).copy())
            cleaned = {}
            for k, v in node.items():
                if k not in ("additionalProperties", "title", "$defs"):
                    cleaned[k] = resolve_node(v)
            return cleaned
        elif isinstance(node, list):
            return [resolve_node(item) for item in node]
        return node

    return resolve_node(raw_schema)


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawRightString(760, 20, f"Page {self._pageNumber} of {page_count}")
        self.drawString(32, 20, "Academic Master Timetable — Auto-Generated Schedule")
        self.restoreState()


# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/parse-excel")
async def parse_excel(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        xls = pd.ExcelFile(io.BytesIO(contents))
        extracted_text = []
        for sheet in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet).dropna(how="all").dropna(axis=1, how="all")
            extracted_text.append(f"=== SHEET: {sheet} ===")
            extracted_text.append(df.to_string(index=False))
        return {"text": "\n\n".join(extracted_text)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/generate")
async def generate_schedule(
        api_key: str = Form(...),
        institution: str = Form(...),
        dept_sem: str = Form(...),
        classes_input: str = Form(...),
        working_days: str = Form(...),
        time_slots: str = Form(...),
        rooms: str = Form(...),
        subgroups: str = Form(...),
        subjects_spec: str = Form(...),
        constraints: str = Form(...)
):
    try:
        client = genai.Client(api_key=api_key)

        system_prompt = """
        You are an academic scheduling system generating a conflict-free master timetable.
        STRICT CONSTRAINTS:
        1. NO CLASHES: No faculty, room, class, or subgroup in multiple places at once.
        2. LAB SESSIONS: 2 consecutive periods. Parallel batches run concurrently across distinct labs.
        3. LONG BREAK: Designated break slot is strictly non-instructional.
        """

        user_prompt = f"""
        Institution: {institution} | Dept: {dept_sem}
        Classes: {classes_input} | Days: {working_days} | Time Slots: {time_slots}
        Rooms: {rooms} | Subgroups: {subgroups}
        Constraints: {constraints}

        WORKLOAD & SUBJECTS:
        {subjects_spec}
        """

        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=to_gemini_schema(MasterTimetableResponse),
                temperature=0.1,
            ),
        )

        return json.loads(response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export-pdf")
async def export_pdf(payload: dict):
    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=32, rightMargin=32, topMargin=32,
                                bottomMargin=36)
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=18,
                                     textColor=colors.HexColor('#0f172a'))
        sub_style = ParagraphStyle('DocSub', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor('#475569'))
        header_style = ParagraphStyle('HeaderCell', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Bold',
                                      textColor=colors.white)
        cell_style = ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=8,
                                    textColor=colors.HexColor('#1e293b'))

        elements = []
        info = payload.get("institution_info", {})
        elements.append(Paragraph(f"<b>{info.get('institution', 'Academic Institution')}</b>", title_style))
        elements.append(Paragraph(f"<b>Department:</b> {info.get('department', 'Schedule')}", sub_style))
        elements.append(Spacer(1, 10))

        for idx, day_data in enumerate(payload.get("timetable", [])):
            if idx > 0: elements.append(PageBreak())
            elements.append(Paragraph(f"📅 <b>{day_data['day']} Master Schedule</b>", title_style))
            elements.append(Spacer(1, 6))

            class_schedules = day_data.get("class_schedules", [])
            if not class_schedules: continue

            headers = ["Class / Division"] + [s["time_slot"] for s in class_schedules[0].get("slots", [])]
            table_data = [[Paragraph(f"<b>{h}</b>", header_style) for h in headers]]

            for class_sched in class_schedules:
                row = [Paragraph(f"<b>{class_sched['class_division']}</b>", cell_style)]
                for slot in class_sched.get("slots", []):
                    if slot.get("is_break"):
                        row.append(Paragraph("<b>LONG BREAK</b>", cell_style))
                    else:
                        txts = [f"{s['subject']}<br/>Fac: {s['faculty']} [{s['room']}]" for s in
                                slot.get("sessions", [])]
                        row.append(Paragraph("<br/><br/>".join(txts) if txts else "—", cell_style))
                table_data.append(row)

            col_w = 728 / max(len(headers), 1)
            pdf_table = Table(table_data, colWidths=[col_w] * len(headers))
            pdf_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f172a')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ]))
            elements.append(pdf_table)

        doc.build(elements, canvasmaker=NumberedCanvas)
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/pdf",
                                 headers={"Content-Disposition": "attachment; filename=Master_Timetable.pdf"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import os
    import uvicorn
    # Render injects the PORT environment variable dynamically
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)