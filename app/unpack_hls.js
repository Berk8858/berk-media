#!/usr/bin/env node
/**
 * =========================================================================
 *  HLS URL ÇIKARMA SCRIPTI (unpack_hls.js)
 * =========================================================================
 *  Bu script, filmmakinesi.to embed sayfalarındaki packed (sıkıştırılmış)
 *  JavaScript kodunu çözerek HLS (HTTP Live Streaming) master.m3u8 URL'ini
 *  çıkarır.
 *
 *  NE İŞE YARAR?
 *  Film/dizi siteleri HLS stream URL'lerini JavaScript ile gizler.
 *  Bu script o JavaScript'i eval edip真实 URL'yi bulur.
 *
 *  KULLANIM:
 *    node unpack_hls.js
 *
 *  Girdi:  /tmp/embed_page.html (embed sayfası HTML'i)
 *  Çıktı:  stdout → HLS master.m3u8 URL'i
 *
 *  ÇIKIŞ KODLARI:
 *    0 = Başarılı (URL bulundu)
 *    1 = Hata (URL bulunamadı)
 *
 *  BAĞIMLILIKLAR:
 *    - Node.js 14+
 *    - /tmp/embed_page.html dosyası (download.py tarafından oluşturulur)
 * =========================================================================
 */

const fs = require('fs');
const vm = require('vm');

// =========================================================================
// HLS DECODE FONKSİYONU
// =========================================================================
/**
 * Embed sayfasındaki JavaScript fonksiyonlarının ürettiği parçaları
 * birleştirerek真实 HLS URL'sini decode eder.
 *
 * Adımlar:
 *   1. Parçaları birleştir
 *   2. ROT13 ile alfabetik karakterleri deşifre et
 *   3. Base64 decode
 *   4. String'i ters çevir
 *   5. Unmix: Her karakteri sabit bir key ile karıştırılmış olmaktan çıkar
 *
 * @param {string[]} parts - Decode edilmiş string parçaları
 * @returns {string|null} - HLS URL'i veya null (başarısız ise)
 */
/**
 * Unmix: Karakterleri sabit key ile karıştırılmış olmaktan çıkarır.
 * Her iki decode sıralaması da aynı unmix işlemini kullanır.
 */
function unmixHLS(str) {
    let result = '';
    for (let i = 0; i < str.length; i++) {
        let cc = str.charCodeAt(i);
        cc = (cc - (399756995 % (i + 5)) + 256) % 256;
        result += String.fromCharCode(cc);
    }
    return result;
}

/**
 * ROT13: Alfabetik karakterleri 13 adım kaydırır.
 */
function rot13(str) {
    return str.replace(/[a-zA-Z]/g, function(c) {
        return String.fromCharCode(
            (c <= 'Z' ? 90 : 122) >= (c = c.charCodeAt(0) + 13) ? c : c - 26
        );
    });
}

/**
 * Decode HLS URL - Farklı embed sayfaları farklı sıralama kullanabilir.
 * İki olası sıralama dener:
 *   Sıralama 1: ROT13 → Base64 → Reverse → Unmix (S01E01 gibi)
 *   Sıralama 2: Reverse → Base64 → ROT13 → Unmix (S02E05 gibi)
 *   Sıralama 3: Base64 → Reverse → ROT13 → Unmix
 *   Sıralama 4: ROT13 → Reverse → Base64 → Unmix
 */
function decodeHLS(parts) {
    const value = parts.join('');

    const orders = [
        // Sıralama 1: ROT13 → Base64 → Reverse → Unmix
        function(v) {
            let r = rot13(v);
            let buf = Buffer.from(r, 'base64');
            r = buf.toString('binary');
            r = r.split('').reverse().join('');
            return unmixHLS(r);
        },
        // Sıralama 2: Reverse → Base64 → ROT13 → Unmix
        function(v) {
            let r = v.split('').reverse().join('');
            let buf = Buffer.from(r, 'base64');
            r = buf.toString('binary');
            r = rot13(r);
            return unmixHLS(r);
        },
        // Sıralama 3: Base64 → Reverse → ROT13 → Unmix
        function(v) {
            let buf = Buffer.from(v, 'base64');
            let r = buf.toString('binary');
            r = r.split('').reverse().join('');
            r = rot13(r);
            return unmixHLS(r);
        },
        // Sıralama 4: ROT13 → Reverse → Base64 → Unmix
        function(v) {
            let r = rot13(v);
            r = r.split('').reverse().join('');
            let buf = Buffer.from(r, 'base64');
            r = buf.toString('binary');
            return unmixHLS(r);
        }
    ];

    for (const fn of orders) {
        try {
            const decoded = fn(value);
            if (decoded && decoded.match(/^https?:\/\//)) return decoded;
        } catch(e) {}
    }
    return null;
}

// =========================================================================
// BASE62 DÖNÜŞTÜRME
// =========================================================================
/**
 * Sayıyı base62 (0-9, a-z, A-Z) formatına çevirir.
 * Dean Edwards packer tarafından kullanılan encoding.
 *
 * @param {number} n - Dönüştürülecek sayı
 * @param {number} base - Sayı sistemi (genellikle 62)
 * @returns {string} - Base62 string
 */
function toBase62(n, base) {
    const chars = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
    if (n === 0) return '0';
    let r = '';
    while (n > 0) {
        r = chars[n % base] + r;
        n = Math.floor(n / base);
    }
    return r;
}

// =========================================================================
// DEAN EDWARDS PACKER ÇÖZÜCÜSÜ
// =========================================================================
/**
 * Dean Edwards packer tarafından sıkıştırılmış JavaScript'i çözer.
 *
 * Packed format:
 *   eval(function(p,a,c,k,e,d){
 *     // base conversion + regex replacement fonksiyonu
 *   }('sıkıştırılmış_kod', base62_base, kelime_sayısı, 'kelime1|kelime2'.split('|')))
 *
 * @param {string} p - Sıkıştırılmış kod (ilk argüman)
 * @param {number} a - Base (genellikle 62)
 * @param {number} c - Kelime sayısı
 * @param {string[]} k - Kelime listesi (base62 keylerine karşılık gelir)
 * @returns {string} - Çözülmüş JavaScript kodu
 */
function unpack(p, a, c, k) {
    // base62 key → kelime eşleme sözlüğü oluştur
    const d = {};
    for (let j = 0; j < c; j++) {
        if (k[j]) d[toBase62(j, a)] = k[j];
    }

    // Sıkıştırılmış koddaki base62 keyleri gerçek kelimelerle değiştir
    let result = p;
    // Uzun keyleri önce değiştir (kısa keylerin içine girmemesi için)
    for (const key of Object.keys(d).sort((x, y) => y.length - x.length)) {
        result = result.replace(
            new RegExp('\\b' + key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'g'),
            d[key]
        );
    }
    return result;
}

// =========================================================================
// EVAL BLOKLARINI BULMA
// =========================================================================
/**
 * HTML içinden eval(function(p,a,c,k,e,d){...}...) bloklarını bulur.
 * Parantez eşleşmesini doğru şekilde takip eder.
 *
 * @param {string} html - HTML içeriği
 * @returns {string[]} - Bulunan eval bloklarının dizisi
 */
function findEvalBlocks(html) {
    const blocks = [];
    let pos = 0;
    while (true) {
        const idx = html.indexOf('eval(function(p,a,c,k,e,d)', pos);
        if (idx < 0) break;

        // Parantez eşleşmesini takip et
        let depth = 0, i = idx, started = false;
        while (i < html.length) {
            if (html[i] === '(') { depth++; started = true; }
            else if (html[i] === ')') {
                depth--;
                if (started && depth === 0) {
                    blocks.push(html.substring(idx, i + 1));
                    break;
                }
            }
            i++;
        }
        pos = idx + 1;
    }
    return blocks;
}

// =========================================================================
// MANUEL ARGÜMAN AYIKLAMA (VM Başarısız Olursa Fallback)
// =========================================================================
/**
 * eval bloğundan argümanları (p, a, c, k) manuel olarak ayıklar.
 * İlk argüman tek tırnak içinde bir string olduğu için özel parse eder.
 *
 * @param {string} block - Tam eval bloğu
 * @returns {object|null} - {p, a, c, k} veya null
 */
function tryUnpackManually(block) {
    const bodyEnd = block.indexOf("}('");
    if (bodyEnd < 0) return null;

    // İlk argümanın başlangıç noktasını bul (' karakteri)
    const argsStart = bodyEnd + 2;

    // İlk string argümanını oku (tek tırnak içinde, \' escape'leri ile)
    let i = argsStart, inStr = true, strChar = "'";
    let p = "'";
    i++; // Açılış tırnağını atla

    while (i < block.length) {
        const ch = block[i];
        if (ch === '\\') {
            p += ch + (block[i + 1] || '');
            i += 2;
            continue;
        }
        if (ch === strChar) {
            inStr = false;
            p += ch;
            i++;
            break;
        }
        p += ch;
        i++;
    }

    // Kalan argümanları oku (a, c, k)
    while (i < block.length && (block[i] === ' ' || block[i] === ',' || block[i] === '\n')) i++;

    let aStr = '';
    while (i < block.length && block[i] !== ',') { aStr += block[i]; i++; }
    i++; // virgülü atla

    while (i < block.length && (block[i] === ' ' || block[i] === ',' || block[i] === '\n')) i++;

    let cStr = '';
    while (i < block.length && block[i] !== ',') { cStr += block[i]; i++; }
    i++; // virgülü atla

    while (i < block.length && (block[i] === ' ' || block[i] === '\n')) i++;

    // k argümanını ayıkla: 'part1|part2|...'.split('|')
    const splitIdx = block.indexOf(".split('|')", i);
    if (splitIdx < 0) return null;
    const kStr = block.substring(i, splitIdx);

    return {
        p: p.replace(/^'|'$/g, ''),   // Açılış/kapanış tırnaklarını temizle
        a: parseInt(aStr.trim()),      // Base (genellikle 62)
        c: parseInt(cStr.trim()),      // Kelime sayısı
        k: kStr.replace(/^'|'$/g, '').split('|')  // Kelime listesi
    };
}

// =========================================================================
// NODE.JS VM İLE EVAL (EN GÜVENLİ YÖNTEM)
// =========================================================================
/**
 * Node.js vm modülü ile eval bloğunu sandbox içinde çalıştırır.
 * Packed JS'in kendi eval'ini executing ederek çözümlemeyi sağlar.
 *
 * @param {string} block - Tam eval bloğu
 * @returns {string|null} - Çözülmüş JavaScript kodu veya null
 */
function tryUnpackVM(block) {
    try {
        // "eval(" prefix'ini ve son ")"'i kaldır
        // Parantez içine al: function statement olmasın, expression olsun
        const inner = '(' + block.slice(5, -1) + ')';

        // atob polyfill: Browser API'si Node.js'de yok
        const atobFn = function(str) {
            return Buffer.from(str, 'base64').toString('binary');
        };

        // Sandbox: atob + gerekli browser-like globals
        const sandbox = {
            atob: atobFn,
            String: String,
            RegExp: RegExp,
            Array: Array,
            parseInt: parseInt,
            parseFloat: parseFloat,
            isNaN: isNaN,
            undefined: undefined,
            NaN: NaN,
            Infinity: Infinity
        };
        vm.createContext(sandbox);
        const result = vm.runInContext(inner, sandbox, { timeout: 5000 });
        return typeof result === 'string' ? result : null;
    } catch(e) {
        return null;
    }
}

// =========================================================================
// HLS URL BULMA
// =========================================================================
/**
 * Verilen metin içinde HLS master.m3u8 URL'ini arar.
 *
 * @param {string} text - Aranacak metin
 * @returns {string|null} - HLS URL'i veya null
 */
function findHLSUrl(text) {
    if (!text) return null;
    const m = text.match(/https?:\/\/[^\s"'<>\\]+\.m3u8[^\s"'<>\\]*/);
    return m ? m[0] : null;
}

// =========================================================================
// DECODE EDİLMİŞ FONKSİYON ÇAĞRISINI BULMA
// =========================================================================
/**
 * Çözülmüş JS kodunda decode fonksiyonu çağrılarını arar.
 * Örnek: dc_xyz123(["part1","part2","part3"])
 *
 * @param {string} unpacked - Çözülmüş JavaScript kodu
 * @returns {string|null} - Decode edilmiş HLS URL veya null
 */
function findDecodedCall(unpacked) {
    const funcCallRe = /(\w+)\(\[([^\]]+)\]\)/g;
    let match;
    while ((match = funcCallRe.exec(unpacked)) !== null) {
        const parts = [...match[2].matchAll(/"([^"]*)"/g)].map(x => x[1]);
        if (parts.length > 2) {
            const decoded = decodeHLS(parts);
            if (decoded && decoded.match(/^https?:\/\//)) return decoded;
        }
    }
    return null;
}


// =========================================================================
// ANA İŞLEV
// =========================================================================
/**
 * Ana program akışı:
 * 1. /tmp/embed_page.html dosyasını oku
 * 2. Doğrudan m3u8 URL'i varsa dön
 * 3. Yoksa eval bloklarını bul ve çöz
 * 4. Çözülmüş kodda HLS URL'ini ara
 * 5. URL bulamazsan decode fonksiyonu çağrısını dene
 */

// Girdi dosyasını oku
const html = fs.readFileSync('/tmp/embed_page.html', 'utf8');

// Yöntem 1: Doğrudan m3u8 URL'i ara (bazı sayfalarda direkt olabiliyor)
const dm = findHLSUrl(html);
if (dm) { console.log(dm); process.exit(0); }

// Yöntem 2: Eval bloklarını çöz
const blocks = findEvalBlocks(html);
for (const block of blocks) {
    // Önce VM ile dene (en güvenilir)
    const vmResult = tryUnpackVM(block);
    if (vmResult) {
        const url = findHLSUrl(vmResult);
        if (url) { console.log(url); process.exit(0); }

        const decoded = findDecodedCall(vmResult);
        if (decoded) { console.log(decoded); process.exit(0); }
    }

    // VM başarısız olursa manuel parse dene
    const args = tryUnpackManually(block);
    if (args) {
        try {
            const unpacked = unpack(args.p, args.a, args.c, args.k);
            const url = findHLSUrl(unpacked);
            if (url) { console.log(url); process.exit(0); }

            const decoded = findDecodedCall(unpacked);
            if (decoded) { console.log(decoded); process.exit(0); }
        } catch(e) {}
    }
}

// Hiçbir yöntem çalışmadı
process.exit(1);
