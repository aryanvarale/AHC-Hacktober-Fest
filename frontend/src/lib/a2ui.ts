/* A2UI: the agent describes a surface declaratively; the client renders it with its own components. */

export interface A2Component {
  type: string;
  props: Record<string, any>;
}

export interface Surface {
  id: string;
  root?: string;
  ready: boolean;
  components: Record<string, A2Component>;
  data: Record<string, any>;
  raw: unknown[];
}

export type A2Message = Record<string, any>;

export function applyA2UI(prev: Surface | undefined, surfaceId: string, msg: A2Message): Surface {
  const s: Surface = prev
    ? { ...prev, raw: [...prev.raw, msg] }
    : { id: surfaceId, ready: false, components: {}, data: {}, raw: [msg] };

  if (msg.surfaceUpdate) {
    const components = { ...s.components };
    for (const c of msg.surfaceUpdate.components as { id: string; component: Record<string, any> }[]) {
      const type = Object.keys(c.component)[0];
      components[c.id] = { type, props: c.component[type] };
    }
    s.components = components;
  }
  if (msg.dataModelUpdate) {
    const { path = "/", contents } = msg.dataModelUpdate;
    s.data = path === "/" ? { ...s.data, ...contents } : setPath(s.data, path, contents);
  }
  if (msg.beginRendering) {
    s.root = msg.beginRendering.root;
    s.ready = true;
  }
  return s;
}

function setPath(data: Record<string, any>, path: string, value: unknown) {
  const keys = path.split("/").filter(Boolean);
  const next = { ...data };
  let cur: any = next;
  keys.forEach((k, i) => {
    if (i === keys.length - 1) cur[k] = value;
    else { cur[k] = { ...(cur[k] ?? {}) }; cur = cur[k]; }
  });
  return next;
}

/** Resolve an A2UI value: a literal, or a { path } binding into the surface's data model. */
export function bound<T = any>(value: any, data: Record<string, any>, fallback?: T): T {
  if (value && typeof value === "object") {
    if ("literalString" in value) return value.literalString as T;
    if ("path" in value) {
      const out = String(value.path).split("/").filter(Boolean).reduce<any>((o, k) => (o == null ? o : o[k]), data);
      return (out ?? fallback) as T;
    }
  }
  return (value ?? fallback) as T;
}

export const fill = (template: string, item: Record<string, any>) =>
  template.replace(/\{(\w+)\}/g, (_, k) => String(item?.[k] ?? ""));
