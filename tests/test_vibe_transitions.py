import unittest
from PIL import Image, ImageChops
from backend.app.vibe_transitions import Entrance, composite, prioritize_entrance

class EntranceTests(unittest.TestCase):
    def test_all_transitions_preserve_endpoints_and_change_middle(self):
        previous=Image.new('RGB',(160,90),'red');current=Image.new('RGB',(160,90),'blue')
        for kind in ('layered','brush','dissolve','black','white','left','right','up','down'):
            with self.subTest(kind=kind):
                self.assertIsNone(ImageChops.difference(composite(previous,current,0,kind),previous).getbbox())
                self.assertIsNone(ImageChops.difference(composite(previous,current,1,kind),current).getbbox())
                self.assertIsNone(ImageChops.difference(composite(previous,current,2,kind),current).getbbox())
                self.assertIsNotNone(ImageChops.difference(composite(previous,current,.5,kind),previous).getbbox())
    def test_direction_keeps_previous_underneath(self):
        old=Image.new('RGB',(100,100),'red');new=Image.new('RGB',(100,100),'blue')
        self.assertEqual(composite(old,new,.5,'left').getpixel((90,50)),(255,0,0))
        self.assertEqual(composite(old,new,.5,'left').getpixel((10,50)),(0,0,255))
    def test_large_layers_and_sixty_percent_arrive_early(self):
        elements=[dict(asset_id=str(i),box=[0,0,100 if i==4 else 10,100]) for i in range(5)]
        motions=[dict(asset_id=str(i),cue='late',seconds=.6,delay=0) for i in range(5)]
        prioritize_entrance(motions,elements,[dict(slide_id='first',at=0)],8)
        early=[m for m in motions if m['cue']=='first']
        self.assertGreaterEqual(len(early),3)
        self.assertIn('4',[m['asset_id'] for m in early])
        self.assertTrue(all(m['seconds']+m['delay']<=1 for m in early))
        self.assertTrue(any(m['cue']=='late' for m in motions))
    def test_duration_validation(self):
        for seconds in (0,-1,4,float('nan')):
            with self.assertRaises(ValueError):Entrance(seconds=seconds)

if __name__=='__main__':unittest.main()
