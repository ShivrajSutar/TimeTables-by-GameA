let generatedData = null;

async function uploadExcel() {
    const fileInput = document.getElementById('excel_file');
    const statusText = document.getElementById('upload_status');
    if (!fileInput.files.length) return;

    statusText.innerText = "Parsing Excel file...";
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    try {
        const response = await fetch('/api/parse-excel', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (response.ok) {
            document.getElementById('subjects_spec').value = data.text;
            statusText.innerText = `Uploaded: ${fileInput.files[0].name}`;
        } else {
            alert(`Error: ${data.detail}`);
            statusText.innerText = "Upload failed";
        }
    } catch (e) {
        alert("Failed to parse Excel sheet.");
        statusText.innerText = "Upload failed";
    }
}

async function generateSchedule() {
    const apiKey = document.getElementById('api_key').value;
    if (!apiKey) {
        alert("Please enter your Gemini API Key.");
        return;
    }

    const payload = new FormData();
    payload.append("api_key", apiKey);
    payload.append("institution", document.getElementById('institution').value);
    payload.append("dept_sem", document.getElementById('dept_sem').value);
    payload.append("classes_input", document.getElementById('classes_input').value);
    payload.append("working_days", document.getElementById('working_days').value);
    payload.append("time_slots", document.getElementById('time_slots').value);
    payload.append("rooms", "Classrooms: 210, 211, 301; Labs: RL1, RL2, RL3");
    payload.append("subgroups", "S1, S2, S3, T1, T2, T3");
    payload.append("subjects_spec", document.getElementById('subjects_spec').value);
    payload.append("constraints", document.getElementById('constraints').value);

    document.getElementById('placeholder_state').classList.add('hidden');
    document.getElementById('result_state').classList.remove('hidden');
    document.getElementById('table_container').innerHTML = `<div class="p-12 text-center text-slate-400">Synthesizing timetable with Gemini 3.8 Flash...</div>`;

    try {
        const response = await fetch('/api/generate', {
            method: 'POST',
            body: payload
        });
        const data = await response.json();

        if (response.ok) {
            generatedData = data;
            renderTimetable(data);
        } else {
            alert(`Error: ${data.detail}`);
        }
    } catch (e) {
        alert("Generation failed.");
    }
}

function renderTimetable(data) {
    const dayTabsContainer = document.getElementById('day_tabs');
    const tableContainer = document.getElementById('table_container');

    dayTabsContainer.innerHTML = '';
    const days = data.timetable || [];

    if (days.length === 0) {
        tableContainer.innerHTML = '<div class="p-6 text-center">No schedule generated.</div>';
        return;
    }

    days.forEach((dayData, index) => {
        const btn = document.createElement('button');
        btn.className = `px-4 py-2 rounded-xl text-xs font-semibold transition ${index === 0 ? 'gradient-btn text-white' : 'bg-slate-900 text-slate-400 hover:text-slate-200'}`;
        btn.innerText = dayData.day;
        btn.onclick = () => selectDay(index, btn);
        dayTabsContainer.appendChild(btn);
    });

    renderDayGrid(days[0]);
}

function selectDay(index, targetBtn) {
    const buttons = document.querySelectorAll('#day_tabs button');
    buttons.forEach(b => b.className = 'px-4 py-2 rounded-xl text-xs font-semibold bg-slate-900 text-slate-400 hover:text-slate-200');
    targetBtn.className = 'px-4 py-2 rounded-xl text-xs font-semibold gradient-btn text-white';

    renderDayGrid(generatedData.timetable[index]);
}

function renderDayGrid(dayData) {
    const classSchedules = dayData.class_schedules || [];
    if (!classSchedules.length) return;

    const sampleSlots = classSchedules[0].slots || [];
    let html = `<table class="tt-grid-table"><thead><tr><th>Class</th>`;

    sampleSlots.forEach(s => {
        html += `<th>${s.time_slot}</th>`;
    });
    html += `</tr></thead><tbody>`;

    classSchedules.forEach(cs => {
        html += `<tr><td class="font-bold text-slate-200">${cs.class_division}</td>`;
        cs.slots.forEach(slot => {
            if (slot.is_break) {
                html += `<td class="break-badge">LONG BREAK</td>`;
            } else {
                let sessionContent = '';
                (slot.sessions || []).forEach(sess => {
                    sessionContent += `<div class="session-badge mb-1">
                        <div class="font-bold text-blue-300">${sess.subject}</div>
                        <div class="text-[10px] text-slate-400">Fac: ${sess.faculty} | Room: ${sess.room}</div>
                    </div>`;
                });
                html += `<td>${sessionContent || '—'}</td>`;
            }
        });
        html += `</tr>`;
    });

    html += `</tbody></table>`;
    document.getElementById('table_container').innerHTML = html;
}

async function exportPDF() {
    if (!generatedData) return;
    try {
        const response = await fetch('/api/export-pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(generatedData)
        });
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = "Master_Timetable.pdf";
        a.click();
    } catch (e) {
        alert("PDF export failed.");
    }
}