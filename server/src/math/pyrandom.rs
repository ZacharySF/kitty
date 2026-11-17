//! CPython 3.12's `random.Random`: the Mersenne Twister (`_randommodule.c`), seeded from a
//! string (version 2: the text's UTF-8 followed by its SHA-512, read as one big-endian
//! integer) or from an integer, and the methods the generators call (`random`,
//! `getrandbits`, `_randbelow`, `randrange`, `randint`, `choice`, `shuffle`, `sample`), in
//! the same order of draws, so `(template_id, seed)` gives Python's problem.

use sha2::{Digest, Sha512};

const N: usize = 624;
const M: usize = 397;

pub struct Random {
    state: [u32; N],
    index: usize,
}

impl Random {
    fn init_genrand(seed: u32) -> Self {
        let mut state = [0u32; N];
        state[0] = seed;
        for i in 1..N {
            state[i] = 1812433253u32.wrapping_mul(state[i - 1] ^ (state[i - 1] >> 30)).wrapping_add(i as u32);
        }
        Random { state, index: N }
    }

    /// `init_by_array`.
    fn from_key(key: &[u32]) -> Self {
        let mut random = Random::init_genrand(19650218);
        let mt = &mut random.state;
        let (mut i, mut j) = (1usize, 0usize);
        for _ in 0..N.max(key.len()) {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1664525)).wrapping_add(key[j]).wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1566083941)).wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
        }
        mt[0] = 0x8000_0000;
        random
    }

    /// `Random(n)` for a non-negative integer given as big-endian bytes: its 32-bit words,
    /// least significant first (at least one).
    fn from_be_bytes(bytes: &[u8]) -> Self {
        let start = bytes.iter().position(|&byte| byte != 0).unwrap_or(bytes.len());
        let digits = &bytes[start..];
        let mut key: Vec<u32> = digits.rchunks(4).map(|chunk| chunk.iter().fold(0u32, |word, &byte| (word << 8) | u32::from(byte))).collect();
        if key.is_empty() {
            key.push(0);
        }
        Random::from_key(&key)
    }

    /// `Random(text)`.
    pub fn from_text(text: &str) -> Self {
        let mut bytes = text.as_bytes().to_vec();
        bytes.extend_from_slice(&Sha512::digest(text.as_bytes()));
        Random::from_be_bytes(&bytes)
    }

    /// `Random(n)` for a non-negative integer.
    pub fn from_int(n: u128) -> Self {
        Random::from_be_bytes(&n.to_be_bytes())
    }

    /// `genrand_uint32`.
    fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            let mt = &mut self.state;
            for k in 0..N {
                let y = (mt[k] & 0x8000_0000) | (mt[(k + 1) % N] & 0x7fff_ffff);
                mt[k] = mt[(k + M) % N] ^ (y >> 1) ^ if y & 1 == 1 { 0x9908_b0df } else { 0 };
            }
            self.index = 0;
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^ (y >> 18)
    }

    /// `random()`: 53 random bits in [0, 1).
    pub fn random(&mut self) -> f64 {
        let a = self.next_u32() >> 5;
        let b = self.next_u32() >> 6;
        (f64::from(a) * 67108864.0 + f64::from(b)) * (1.0 / 9007199254740992.0)
    }

    /// `getrandbits(k)` for `k` up to 64: whole words least significant first, the last one
    /// cut to the bits still needed.
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        assert!(k <= 64);
        if k == 0 {
            return 0;
        }
        if k <= 32 {
            return u64::from(self.next_u32() >> (32 - k));
        }
        let low = u64::from(self.next_u32());
        let high = u64::from(self.next_u32() >> (64 - k));
        (high << 32) | low
    }

    /// `_randbelow(n)` for `n > 0`.
    pub fn randbelow(&mut self, n: u64) -> u64 {
        assert!(n > 0, "empty range");
        let k = 64 - n.leading_zeros();
        let mut r = self.getrandbits(k);
        while r >= n {
            r = self.getrandbits(k);
        }
        r
    }

    /// `randrange(start, stop)`.
    pub fn randrange(&mut self, start: i64, stop: i64) -> i64 {
        assert!(stop > start, "empty range in randrange");
        start + self.randbelow((stop - start) as u64) as i64
    }

    /// `randint(a, b)`.
    pub fn randint(&mut self, a: i64, b: i64) -> i64 {
        self.randrange(a, b + 1)
    }

    /// `choice(items)`.
    pub fn choice<T: Clone>(&mut self, items: &[T]) -> T {
        items[self.randbelow(items.len() as u64) as usize].clone()
    }

    /// `shuffle(items)`.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = self.randbelow(i as u64 + 1) as usize;
            items.swap(i, j);
        }
    }

    /// `sample(items, k)`.
    pub fn sample<T: Clone>(&mut self, items: &[T], k: usize) -> Vec<T> {
        let n = items.len();
        assert!(k <= n, "Sample larger than population or is negative");
        let mut setsize = 21usize;
        if k > 5 {
            // `4 ** _ceil(_log(k * 3, 4))`, the logarithms as floats.
            setsize += 4usize.pow((((k * 3) as f64).ln() / 4f64.ln()).ceil() as u32);
        }
        let mut out = Vec::with_capacity(k);
        if n <= setsize {
            let mut pool = items.to_vec();
            for i in 0..k {
                let j = self.randbelow((n - i) as u64) as usize;
                out.push(pool[j].clone());
                pool[j] = pool[n - i - 1].clone();
            }
        } else {
            let mut selected = std::collections::HashSet::new();
            for _ in 0..k {
                let mut j = self.randbelow(n as u64) as usize;
                while selected.contains(&j) {
                    j = self.randbelow(n as u64) as usize;
                }
                selected.insert(j);
                out.push(items[j].clone());
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn draws_match_cpython() {
        let mut random = Random::from_int(42);
        assert_eq!(random.random(), 0.6394267984578837);
        assert_eq!(random.getrandbits(64), 13679192365072849617);
        assert_eq!(random.randint(1, 6), 3);
        assert_eq!(Random::from_text("a").random(), 0.2720295377534757);
        let mut random = Random::from_int(u128::from(u64::MAX));
        assert_eq!(random.sample(&(0..30).collect::<Vec<i64>>(), 7), [0, 7, 10, 19, 6, 14, 26]);
        assert_eq!(random.sample(&['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'], 3), ['b', 'c', 'f']);
    }
}
