"""Getting a picture ready to keep (#3274): no location data left, odd and huge and broken files
refused, a small preview. Pictures are made in memory; nothing real."""

from __future__ import annotations

import io
import unittest

from PIL import Image

from dmbot.pictures.prepare import MAX_PIXELS, PREVIEW_PX, Refused, prepare, prepare_async

ORIENTATION, GPS_IFD = 0x0112, 0x8825


def jpeg_with_gps(size: tuple[int, int] = (120, 60), orientation: int = 1) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[ORIENTATION] = orientation
    exif[0x010F] = "ExampleCam"  # make
    gps = exif.get_ifd(GPS_IFD)
    gps[1], gps[2] = "N", (51.0, 30.0, 0.0)  # latitude, a made-up place
    gps[3], gps[4] = "W", (0.0, 7.0, 0.0)
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif, icc_profile=b"x" * 40)
    return out.getvalue()


def frame_count(data: bytes) -> int:
    return int(getattr(Image.open(io.BytesIO(data)), "n_frames", 1))


def png(size: tuple[int, int] = (50, 40), text: bool = False) -> bytes:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    if text:
        info.add_text("Author", "Someone Real")
    out = io.BytesIO()
    Image.new("RGBA", size, (0, 200, 0, 255)).save(out, "PNG", pnginfo=info if text else None)
    return out.getvalue()


class Stripped(unittest.TestCase):
    def test_a_jpeg_with_a_location_comes_out_with_none(self) -> None:
        source = jpeg_with_gps()
        self.assertIn(b"Exif", source)
        self.assertIsNotNone(Image.open(io.BytesIO(source)).getexif().get_ifd(GPS_IFD))
        got = prepare(source)
        self.assertEqual(got.ext, "jpg")
        self.assertNotIn(b"Exif", got.data)
        self.assertEqual(len(Image.open(io.BytesIO(got.data)).getexif()), 0)
        self.assertNotIn("icc_profile", Image.open(io.BytesIO(got.data)).info)

    def test_it_is_turned_upright_before_the_tag_goes(self) -> None:
        got = prepare(jpeg_with_gps((100, 50), orientation=6))  # a portrait photo stored sideways
        self.assertEqual((got.width, got.height), (50, 100))
        self.assertEqual(Image.open(io.BytesIO(got.data)).size, (50, 100))

    def test_text_chunks_of_a_png_are_gone(self) -> None:
        got = prepare(png(text=True))
        self.assertEqual(got.ext, "png")
        self.assertNotIn(b"Someone Real", got.data)

    def test_the_format_is_kept(self) -> None:
        out = io.BytesIO()
        Image.new("RGB", (30, 30), (1, 2, 3)).save(out, "WEBP")
        self.assertEqual(prepare(out.getvalue()).ext, "webp")

    def test_an_odd_format_becomes_a_png(self) -> None:
        out = io.BytesIO()
        Image.new("RGB", (30, 30), (1, 2, 3)).save(out, "BMP")
        self.assertEqual(prepare(out.getvalue()).ext, "png")

    def test_an_animated_gif_keeps_its_frames(self) -> None:
        out = io.BytesIO()
        frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
        got = prepare(out.getvalue())
        self.assertEqual(got.ext, "gif")
        self.assertEqual(frame_count(got.data), 3)

    def test_a_colour_profile_in_a_png_does_not_survive(self) -> None:
        out = io.BytesIO()
        Image.new("RGB", (20, 20), (4, 5, 6)).save(out, "PNG", icc_profile=b"profile-bytes" * 4)
        self.assertIn("icc_profile", Image.open(io.BytesIO(out.getvalue())).info)
        got = prepare(out.getvalue())
        self.assertNotIn("icc_profile", Image.open(io.BytesIO(got.data)).info)
        self.assertNotIn(b"iCCP", got.data)

    def test_a_still_gif_with_a_comment_keeps_none(self) -> None:
        out = io.BytesIO()
        Image.new("P", (20, 20)).save(out, "GIF", comment=b"GIFSECRET")
        self.assertIn(b"GIFSECRET", out.getvalue())
        self.assertNotIn(b"GIFSECRET", prepare(out.getvalue()).data)

    def test_an_animated_gif_comment_is_dropped_and_timing_kept(self) -> None:
        out = io.BytesIO()
        frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
        frames[0].save(
            out,
            "GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[50, 200],
            comment=b"GIFSECRET",
        )
        got = prepare(out.getvalue())
        self.assertNotIn(b"GIFSECRET", got.data)
        again = Image.open(io.BytesIO(got.data))
        self.assertEqual(frame_count(got.data), 2)
        self.assertEqual(again.info["duration"], 50)

    def test_the_same_file_has_the_same_fingerprint(self) -> None:
        source = png()
        self.assertEqual(prepare(source).sha256, prepare(source).sha256)
        self.assertNotEqual(prepare(source).sha256, prepare(png((51, 40))).sha256)


class Refusals(unittest.TestCase):
    def test_a_decompression_bomb_is_refused_before_it_is_decoded(self) -> None:
        side = int(MAX_PIXELS**0.5) + 100
        bomb = io.BytesIO()
        Image.new("1", (side, side)).save(bomb, "PNG")  # tiny file, huge picture
        self.assertLess(len(bomb.getvalue()), 1_000_000)
        with self.assertRaises(Refused) as caught:
            prepare(bomb.getvalue())
        self.assertEqual(str(caught.exception), "pixels")

    def test_a_corrupt_file_is_refused(self) -> None:
        for bad in (b"not a picture at all", png()[:60], b"\x89PNG\r\n\x1a\n" + b"\x00" * 30):
            with self.assertRaises(Refused):
                prepare(bad)

    def test_an_empty_file_is_refused(self) -> None:
        with self.assertRaises(Refused):
            prepare(b"")

    def test_a_gif_whose_frames_together_are_too_big_is_refused(self) -> None:
        # a big canvas times many frames would be held in memory all at once
        out = io.BytesIO()
        frames = [Image.new("1", (2000, 2000), n % 2) for n in range(20)]  # 80 million pixels
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
        with self.assertRaises(Refused) as caught:
            prepare(out.getvalue())
        self.assertEqual(str(caught.exception), "frames")

    def test_a_gif_with_too_many_frames_is_refused(self) -> None:
        out = io.BytesIO()
        frames = [Image.new("L", (4, 4), n % 256) for n in range(205)]
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
        with self.assertRaises(Refused):
            prepare(out.getvalue())


class Preview(unittest.TestCase):
    def test_it_is_at_most_400_on_the_long_side(self) -> None:
        for size in ((1200, 800), (300, 900), (50, 40)):
            out = io.BytesIO()
            Image.new("RGB", size, (9, 9, 9)).save(out, "PNG")
            preview = Image.open(io.BytesIO(prepare(out.getvalue()).preview))
            self.assertEqual(preview.format, "WEBP")
            self.assertLessEqual(max(preview.size), PREVIEW_PX)

    def test_a_small_picture_is_not_enlarged(self) -> None:
        preview = Image.open(io.BytesIO(prepare(png((50, 40))).preview))
        self.assertEqual(preview.size, (50, 40))


class Threaded(unittest.IsolatedAsyncioTestCase):
    async def test_the_async_wrapper_gives_the_same_answer(self) -> None:
        source = jpeg_with_gps()
        self.assertEqual((await prepare_async(source)).data, prepare(source).data)


if __name__ == "__main__":
    unittest.main()
