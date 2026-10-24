import { createNoticeHandler } from './handler.mjs';

// 공지 공개 게시는 운영자가 허락한 것이다. 로그인 정보는 서버에만 둔다.
Deno.serve(
    createNoticeHandler({
        supabaseUrl: Deno.env.get('SUPABASE_URL')!,
        serviceKey: Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
        cookieKey: Deno.env.get('BADGES_COOKIE_KEY') || '',
    })
);
