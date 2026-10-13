import { createNoticeHandler } from './handler.mjs';

// Public notice publication is operator-authorized. Credentials stay server-side.
Deno.serve(createNoticeHandler({
  supabaseUrl: Deno.env.get('SUPABASE_URL')!,
  serviceKey: Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
  cookieKey: Deno.env.get('BADGES_COOKIE_KEY') || '',
}));
