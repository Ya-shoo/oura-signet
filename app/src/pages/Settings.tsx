// Settings — your profile is the ground truth for the estimate cards. VO₂max,
// BMR and BMI are pure functions of age / sex / weight (+ height for BMI), so the
// moment you enter real numbers those cards become *yours*, not a population guess.
// The panel on the right previews exactly what the home cards will show once saved.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Profile, ProfileInput, Units } from "../lib/api";
import { api } from "../lib/api";
import { useProfile, useDataStatus } from "../lib/hooks";
import { Icon } from "../components/icons";
import {
  vo2maxJackson, bmrMifflin, vo2Category, bmiOf, bmiClass,
  kgToLb, lbToKg, cmToFtIn, ftInToCm,
} from "../lib/estimates";

// Form state carries display strings (so fields stay freely editable) + the two
// enum choices; canonical metric values are derived on demand.
interface Form {
  units: Units;
  sex: "M" | "F";
  age: string;
  weight: string; // kg when metric, lb when imperial
  heightCm: string; // used when metric
  ft: string;
  inch: string; // used when imperial
}

const round1 = (n: number) => Math.round(n * 10) / 10;

function formFromProfile(p: Profile): Form {
  const { ft, inch } = cmToFtIn(p.heightCm);
  return {
    units: p.units,
    sex: p.sex,
    age: String(Math.round(p.age)),
    weight: p.units === "imperial" ? String(Math.round(kgToLb(p.weightKg))) : String(round1(p.weightKg)),
    heightCm: String(Math.round(p.heightCm)),
    ft: String(ft),
    inch: String(inch),
  };
}

const num = (s: string) => {
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : 0;
};

function canonical(f: Form) {
  const weightKg = f.units === "imperial" ? lbToKg(num(f.weight)) : num(f.weight);
  const heightCm = f.units === "imperial" ? ftInToCm(num(f.ft), num(f.inch)) : num(f.heightCm);
  return { age: num(f.age), female: f.sex === "F", weightKg, heightCm };
}

export function Settings() {
  const profile = useProfile();
  const dataStatus = useDataStatus();
  const qc = useQueryClient();
  const [form, setForm] = useState<Form | null>(null);

  // Seed the form once the saved profile loads (and never clobber edits after).
  useEffect(() => {
    if (profile.data && !form) setForm(formFromProfile(profile.data));
  }, [profile.data, form]);

  const save = useMutation({
    mutationFn: (input: ProfileInput) => api.saveProfile(input),
    onSuccess: (saved) => {
      qc.setQueryData(["profile"], saved);
      qc.invalidateQueries({ queryKey: ["homeChannels"] });
      qc.invalidateQueries({ queryKey: ["vo2History"] });
      setForm(formFromProfile(saved));
    },
  });

  const live = useMemo(() => {
    if (!form) return null;
    const c = canonical(form);
    const vo2 = vo2maxJackson(c.age, c.female, c.weightKg);
    return {
      c,
      vo2: Math.round(vo2),
      cat: vo2Category(vo2, c.age, c.female),
      bmr: Math.round(bmrMifflin(c.age, c.female, c.weightKg, c.heightCm)),
      bmi: bmiOf(c.weightKg, c.heightCm),
    };
  }, [form]);

  if (!form || !live) {
    return (
      <main className="main">
        <div className="phead"><h1>Profile &amp; Settings</h1></div>
        <div className="card">Loading your profile…</div>
      </main>
    );
  }

  const set = (patch: Partial<Form>) => setForm((f) => (f ? { ...f, ...patch } : f));

  // Toggling units only reformats the display strings — canonical value is preserved.
  const switchUnits = (next: Units) => {
    if (next === form.units) return;
    const c = canonical(form);
    const { ft, inch } = cmToFtIn(c.heightCm);
    set({
      units: next,
      weight: next === "imperial" ? String(Math.round(kgToLb(c.weightKg))) : String(round1(c.weightKg)),
      heightCm: String(Math.round(c.heightCm)),
      ft: String(ft),
      inch: String(inch),
    });
  };

  const saved = profile.data;
  const dirty =
    !saved ||
    Math.abs(live.c.age - saved.age) > 0.01 ||
    (live.c.female ? "F" : "M") !== saved.sex ||
    Math.abs(live.c.weightKg - saved.weightKg) > 0.05 ||
    Math.abs(live.c.heightCm - saved.heightCm) > 0.5 ||
    form.units !== saved.units;

  const onSave = () =>
    save.mutate({
      age: live.c.age,
      sex: form.sex,
      weightKg: live.c.weightKg,
      heightCm: live.c.heightCm,
      units: form.units,
    });

  return (
    <main className="main">
      <div className="phead">
        <h1>Profile &amp; Settings</h1>
        <span className="sub">Your numbers, kept on this Mac — they drive the estimate cards below.</span>
      </div>

      <div className="cols">
        {/* LEFT — the editable profile */}
        <div>
          <div className="card">
            <h2>About you</h2>

            <div className="field">
              <label>Units</label>
              <Seg
                value={form.units}
                options={[["imperial", "lb / ft"], ["metric", "kg / cm"]]}
                onChange={(v) => switchUnits(v as Units)}
              />
            </div>

            <div className="field">
              <label>Sex <span className="hintlab">used by VO₂max, BMR &amp; the sleep model</span></label>
              <Seg
                value={form.sex}
                options={[["M", "Male"], ["F", "Female"]]}
                onChange={(v) => set({ sex: v as "M" | "F" })}
              />
            </div>

            <div className="field">
              <label>Age</label>
              <div className="inrow">
                <input className="inp" type="number" inputMode="numeric" min={1} max={120}
                  value={form.age} onChange={(e) => set({ age: e.target.value })} />
                <span className="suffix">years</span>
              </div>
            </div>

            <div className="field">
              <label>Weight</label>
              <div className="inrow">
                <input className="inp" type="number" inputMode="decimal" min={0}
                  value={form.weight} onChange={(e) => set({ weight: e.target.value })} />
                <span className="suffix">{form.units === "imperial" ? "lb" : "kg"}</span>
              </div>
            </div>

            <div className="field">
              <label>Height <span className="hintlab">for BMI &amp; BMR (not in the VO₂max formula)</span></label>
              {form.units === "imperial" ? (
                <div className="inrow">
                  <input className="inp short" type="number" inputMode="numeric" min={0} max={8}
                    value={form.ft} onChange={(e) => set({ ft: e.target.value })} />
                  <span className="suffix">ft</span>
                  <input className="inp short" type="number" inputMode="numeric" min={0} max={11}
                    value={form.inch} onChange={(e) => set({ inch: e.target.value })} />
                  <span className="suffix">in</span>
                </div>
              ) : (
                <div className="inrow">
                  <input className="inp" type="number" inputMode="numeric" min={0}
                    value={form.heightCm} onChange={(e) => set({ heightCm: e.target.value })} />
                  <span className="suffix">cm</span>
                </div>
              )}
            </div>

            <div className="savebar">
              <button className="btn" onClick={onSave} disabled={!dirty || save.isPending}>
                <Icon name="check" sw={2.1} />
                {save.isPending ? "Saving…" : dirty ? "Save changes" : "Saved"}
              </button>
              {dirty && saved && (
                <button className="btn ghost" onClick={() => setForm(formFromProfile(saved))} disabled={save.isPending}>
                  Discard
                </button>
              )}
              {dirty ? (
                <span className="savehint dirty">Unsaved · your home cards update on save</span>
              ) : (
                <span className="savehint">In sync with your home cards</span>
              )}
            </div>
            {save.isError && <div className="caveat" style={{ marginTop: 10 }}>* couldn't save: {String(save.error)}</div>}
          </div>
        </div>

        {/* RIGHT — the estimates these inputs drive */}
        <div>
          <div className="card">
            <h2>Estimates from your profile{dirty ? " · preview" : ""}</h2>

            <div className="est">
              <span className="fic" style={{ background: "var(--activity-tint)", color: "var(--activity)" }}>
                <Icon name="vo2" sw={2} />
              </span>
              <div className="estbody">
                <div className="estlab">VO₂max</div>
                <div className="estnote">{live.cat} · population estimate (Jackson, age·sex·weight)</div>
              </div>
              <div className="estval">{live.vo2}<small>ml/kg</small></div>
            </div>

            <div className="est">
              <span className="fic" style={{ background: "var(--body-tint)", color: "var(--body)" }}>
                <Icon name="flame" sw={2} />
              </span>
              <div className="estbody">
                <div className="estlab">Resting metabolism (BMR)</div>
                <div className="estnote">Schofield · the resting half of your Calories card</div>
              </div>
              <div className="estval">{live.bmr.toLocaleString()}<small>kcal/day</small></div>
            </div>

            <div className="est">
              <span className="fic" style={{ background: "var(--recovery-tint)", color: "var(--recovery)" }}>
                <Icon name="body" sw={2} />
              </span>
              <div className="estbody">
                <div className="estlab">BMI</div>
                <div className="estnote">{live.bmi != null ? bmiClass(live.bmi) : "enter height"}</div>
              </div>
              <div className="estval">{live.bmi != null ? round1(live.bmi) : "—"}</div>
            </div>

            <div className="estfoot">
              These are anthropometric estimates, not measurements. A guided walk or step test
              (coming) reads your live heart rate to compute a VO₂max that's truly yours — and starts
              your trend line.
            </div>
          </div>

          <div className="card">
            <h2>Data &amp; privacy</h2>
            <div className="drow"><span>Account</span><b className="ok">None · fully local</b></div>
            <div className="drow"><span>Profile stored in</span><b>metrics.db</b></div>
            <div className="drow" style={{ borderBottom: "none" }}>
              <span>Path</span>
              <b style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {dataStatus.data?.derivedDbPath ?? "—"}
              </b>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

function Seg({
  value, options, onChange,
}: {
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <div className="seg">
      {options.map(([v, label]) => (
        <button
          key={v}
          className={`segbtn ${value === v ? "on" : ""}`}
          onClick={() => onChange(v)}
          type="button"
        >
          {label}
        </button>
      ))}
    </div>
  );
}
